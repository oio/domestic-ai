# improved_init.py
import asyncio
import logging
import sys
import signal
import init_functions as startup
import os
import time
import psutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("main")

# Flag to track if shutdown is in progress
shutdown_in_progress = False

async def initialize_services():
    """Initialize all services in the correct order"""
    logger.info("Starting domestic-ai initialization...")
    
    # Step 1: Wait for API first (most critical)
    logger.info("Step 1: Waiting for API to be available...")
    api_success = await startup.wait_for_api()
    
    if not api_success:
        logger.critical("API service not available - cannot continue")
        return False
    
    logger.info("API service is available")
    
    # Step 2: Start all tools
    logger.info("Step 2: Starting tool services...")
    tools_success = await startup.start_tools()
    
    if not tools_success:
        logger.warning("Some tool services failed to start")
        # Continue anyway as the bot might still work
    else:
        logger.info("All tool services started successfully")
    
    # Step 3: Start the bot
    logger.info("Step 3: Starting bot service...")
    await startup.start_bot()
    logger.info("Bot started")
    
    logger.info("Initialization complete - system is now running")
    return True

async def forceful_kill_processes():
    """Force kill any remaining Python processes under DOMESTIC_AI_PATH"""
    path = os.environ.get('DOMESTIC_AI_PATH', '.')
    killed = []
    
    logger.info(f"Forcefully killing any remaining processes under {path}")
    
    # First attempt: Kill by finding Python processes with DOMESTIC_AI_PATH in command line
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'python' in proc.name().lower():
                    cmdline = proc.cmdline()
                    if cmdline and any(path in cmd for cmd in cmdline):
                        logger.info(f"Killing Python process {proc.pid}: {' '.join(cmdline[:2])}...")
                        try:
                            # On Unix, try to kill the process group first
                            if os.name != 'nt':
                                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                                logger.info(f"Killed process group for {proc.pid}")
                            else:
                                proc.kill()
                            killed.append(proc.pid)
                        except Exception as e:
                            logger.error(f"Failed to kill process {proc.pid}: {e}")
                            # Fallback to direct kill if process group kill fails
                            try:
                                proc.kill()
                                killed.append(proc.pid)
                            except Exception as e2:
                                logger.error(f"Failed to directly kill process {proc.pid}: {e2}")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
                pass
    except Exception as e:
        logger.error(f"Error during forceful Python process kill: {e}")
    
    # Second attempt: Use system commands to find and kill processes
    try:
        if os.name != 'nt':  # Unix-like systems
            # Find Python processes with the DOMESTIC_AI_PATH in their command line
            ps_cmd = f"ps aux | grep python | grep '{path}' | awk '{{print $2}}'"
            logger.info(f"Running system command: {ps_cmd}")
            
            result = os.popen(ps_cmd).read().strip()
            if result:
                pids = result.split('\n')
                for pid_str in pids:
                    try:
                        pid = int(pid_str)
                        # Kill process groups
                        os.system(f"kill -9 -{os.getpgid(pid)} 2>/dev/null")
                        logger.info(f"Killed process group for PID {pid} via system command")
                        killed.append(pid)
                    except (ValueError, ProcessLookupError) as e:
                        logger.error(f"Error killing PID {pid_str}: {e}")
    except Exception as e:
        logger.error(f"Error during system command kill: {e}")
    
    # Third attempt: In case specific tools don't get killed, target known ports
    try:
        for port in [8000, 8008, 8042]:  # API, Rembg, Image Generation
            proc = startup.find_process_by_port(port)
            if proc:
                logger.info(f"Found process using port {port} (PID: {proc.pid}), killing it...")
                try:
                    proc.kill()
                    killed.append(proc.pid)
                except Exception as e:
                    logger.error(f"Error killing process on port {port}: {e}")
    except Exception as e:
        logger.error(f"Error during port-based kill: {e}")
    
    logger.info(f"Forcefully killed {len(killed)} processes: {killed}")
    return len(killed) > 0

async def verify_shutdown():
    """Verify that all processes have been shut down"""
    path = os.environ.get('DOMESTIC_AI_PATH', '.')
    running = []
    
    # Check for any Python processes with our path
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if 'python' in proc.name().lower():
                cmdline = proc.cmdline()
                if cmdline and any(path in cmd for cmd in cmdline):
                    running.append((proc.pid, ' '.join(cmdline[:2])))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    # Also check for processes on our known ports
    for port in [8000, 8008, 8042]:
        proc = startup.find_process_by_port(port)
        if proc:
            try:
                running.append((proc.pid, f"Process on port {port}"))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    
    if running:
        logger.error(f"Found {len(running)} processes still running after shutdown:")
        for pid, cmd in running:
            logger.error(f"  PID {pid}: {cmd}")
        return False
    else:
        logger.info("All processes successfully terminated")
        return True

async def shutdown():
    """Properly shut down all services"""
    global shutdown_in_progress
    
    if shutdown_in_progress:
        logger.info("Shutdown already in progress, skipping")
        return
    
    shutdown_in_progress = True
    logger.info("Shutting down all services...")
    
    try:
        # Create a signal file to let the bot know it should shut down
        signal_file = os.path.join(os.environ.get('DOMESTIC_AI_PATH', '.'), "bot_shutdown.signal")
        with open(signal_file, 'w') as f:
            f.write(str(time.time()))
        
        logger.info("Created shutdown signal file")
        
        # Wait a moment for the bot to detect the signal
        await asyncio.sleep(2)
        
        # Call the stop function to terminate services
        logger.info("Stopping all services with graceful shutdown...")
        await startup.stop_all_services()
        
        # Give processes a short time to terminate gracefully
        await asyncio.sleep(1)
        
        # Force kill any remaining processes
        await forceful_kill_processes()
        
        # Double-check if everything was killed
        if not await verify_shutdown():
            logger.warning("Some processes still running, trying more aggressive shutdown...")
            # Try a second time with more aggressive approach
            await forceful_kill_processes()
            
            # Final verification
            if not await verify_shutdown():
                logger.error("Failed to kill all processes, some may still be running")
        
        # Make sure the signal file is removed
        if os.path.exists(signal_file):
            try:
                os.remove(signal_file)
                logger.info("Removed shutdown signal file")
            except Exception as e:
                logger.error(f"Failed to remove signal file: {e}")
                
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Emergency fallback - try to kill Python processes directly
        try:
            path = os.environ.get('DOMESTIC_AI_PATH', '.')
            killed = []
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    if 'python' in proc.name().lower():
                        cmdline = proc.cmdline()
                        if cmdline and any(path in cmd for cmd in cmdline):
                            proc.kill()
                            killed.append(proc.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            logger.info(f"Emergency killed {len(killed)} Python processes: {killed}")
        except Exception as e2:
            logger.error(f"Error during emergency kill: {e2}")
    finally:
        logger.info("Domestic-ai terminated")
        # Exit the process directly to ensure we don't hang
        os._exit(0)

def handle_signals():
    """Set up signal handlers for graceful shutdown"""
    loop = asyncio.get_event_loop()
    
    # Define the handler
    def signal_handler(sig_name):
        logger.info(f"{sig_name} signal received")
        # Schedule the shutdown task
        loop.create_task(shutdown())
    
    # Register for SIGINT (Ctrl+C) and SIGTERM
    for sig, name in [(signal.SIGINT, "SIGINT"), (signal.SIGTERM, "SIGTERM")]:
        loop.add_signal_handler(sig, lambda s=name: signal_handler(s))
    
    logger.info("Signal handlers registered for SIGINT and SIGTERM")

async def main():
    """Main entry point with proper signal handling"""
    try:
        # Register signal handlers
        handle_signals()
        
        success = await initialize_services()
        if not success:
            logger.error("Initialization failed")
            await shutdown()
            return
        
        # Create a never-ending task that we can cancel
        done = asyncio.Event()
        
        # Run until we get a termination signal or exception
        await done.wait()
            
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received in main loop")
    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    except Exception as e:
        logger.critical(f"Unexpected error: {e}")
        import traceback
        logger.critical(traceback.format_exc())
    finally:
        await shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # If we get here, the signal handler didn't catch it
        print("\nKeyboard interrupt caught outside event loop, performing emergency shutdown...")
        try:
            # Run a sync version of forceful process kill
            path = os.environ.get('DOMESTIC_AI_PATH', '.')
            # Use system commands for maximum effectiveness
            if os.name != 'nt':  # Unix-like systems (macOS, Linux)
                os.system(f"ps aux | grep python | grep '{path}' | awk '{{print $2}}' | xargs -I{{}} kill -9 {{}} 2>/dev/null")
                print("Used system command to kill Python processes")
            
            # Also try psutil as backup
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if 'python' in proc.name().lower():
                        cmdline = proc.cmdline()
                        if cmdline and any(path in cmd for cmd in cmdline):
                            print(f"Killing Python process {proc.pid}")
                            proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            print("Emergency shutdown completed")
        except Exception as e:
            print(f"Error during emergency shutdown: {e}")
        finally:
            sys.exit(1)