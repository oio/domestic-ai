# improved_init.py
import asyncio
import logging
import sys
import signal
import init_functions as startup
import os
import time

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

async def shutdown():
    """Properly shut down all services"""
    global shutdown_in_progress
    
    if shutdown_in_progress:
        return
    
    shutdown_in_progress = True
    logger.info("Shutting down all services...")
    
    try:
        # First, create a signal file to let the bot know it should shut down
        signal_file = os.path.join(os.environ.get('DOMESTIC_AI_PATH', '.'), "bot_shutdown.signal")
        with open(signal_file, 'w') as f:
            f.write(str(time.time()))
        
        # Wait a moment for the bot to detect the signal
        await asyncio.sleep(2)
        
        # Now call the proper shutdown function
        success = await startup.stop_all_services()
        if success:
            logger.info("All services stopped successfully")
        else:
            logger.warning("Some services may not have stopped properly")
            
        # Make sure the signal file is removed
        if os.path.exists(signal_file):
            try:
                os.remove(signal_file)
            except Exception as e:
                logger.error(f"Failed to remove signal file: {e}")
                
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
    finally:
        # Additional cleanup of any lingering bot processes
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.cmdline()
                    if any("python" in cmd.lower() for cmd in cmdline) and any("bot.py" in cmd for cmd in cmdline):
                        logger.info(f"Found lingering Bot process (PID: {proc.pid}), stopping it")
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except psutil.TimeoutExpired:
                            proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.error(f"Error during final cleanup: {e}")
            
        logger.info("Domestic-ai terminated")

async def main():
    """Main entry point with proper signal handling"""
    try:
        success = await initialize_services()
        if not success:
            logger.error("Initialization failed")
            return
        
        # Create a never-ending task that we can cancel
        pending_forever = asyncio.create_task(asyncio.Event().wait())
        
        # Run until we get a termination signal or exception
        try:
            await pending_forever
        except asyncio.CancelledError:
            logger.info("Main task cancelled")
            
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.critical(f"Unexpected error: {e}")
        import traceback
        logger.critical(traceback.format_exc())
    finally:
        await shutdown()

def handle_signals():
    """Set up signal handlers for graceful shutdown"""
    loop = asyncio.get_event_loop()
    
    # Define the handler
    def signal_handler():
        logger.info("Termination signal received")
        # Cancel the main task
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()
        
        # Set the event to trigger an orderly shutdown
        loop.create_task(shutdown())
    
    # Register for SIGINT (Ctrl+C) and SIGTERM
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

if __name__ == "__main__":
    # Set up signal handlers
    handle_signals()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # This should be caught by the signal handler, but just in case
        pass