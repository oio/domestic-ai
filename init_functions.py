import aiohttp
import asyncio
import dotenv
import logging
import os
import psutil
import subprocess
import time
import signal
from typing import Dict, List, Optional, Set

dotenv.load_dotenv()
logger = logging.getLogger('discord')

# Constants
STARTUP_TIMEOUT = 60
DOMESTIC_AI_PATH = os.environ['DOMESTIC_AI_PATH']
API_HOST = "0.0.0.0"
API_PORT = 8000
API_ENDPOINT = "/api_endpoints"

# Global tracking of all child processes
child_processes = set()
bot_process = None

class Startup:
    """Class to represent a service (API or tool)"""
    def __init__(self, 
                 name: str, 
                 port: int, 
                 host: str = "localhost", 
                 endpoint: str = "/", 
                 command_path: str = None,
                 startup_timeout: int = STARTUP_TIMEOUT):
        self.name = name
        self.port = port
        self.host = host
        self.url = f"http://{host}:{port}{endpoint}"
        self.command_path = command_path
        self.startup_timeout = startup_timeout
        self.process = None  # Store process reference

    async def is_running(self, session: aiohttp.ClientSession, timeout: int = 2) -> bool:
        """Check if the service is running"""
        if self.port is None:
            # If no port is specified (e.g., for the Bot), check if process is running
            return self.process is not None and self.process.poll() is None
            
        try:
            async with session.get(self.url, timeout=timeout) as response:
                return response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def start(self) -> bool:
        """Start the service if it's not already running"""
        global bot_process, child_processes
        
        if not self.command_path:
            logger.warning(f"No command path specified for {self.name}, cannot start")
            return False

        try:
            # Special handling for the Bot
            if self.name == "Bot":
                logger.info(f"Starting {self.name} process with command: {self.command_path}")
                # Launch the bot process and store its reference
                self.process = subprocess.Popen(['bash', self.command_path], 
                                             stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL,
                                             preexec_fn=os.setsid if os.name != 'nt' else None)
                
                # Track this process globally
                bot_process = self.process
                child_processes.add(self.process.pid)
                
                await asyncio.sleep(5)  # Give it time to start
                
                # Check if process is still running
                if self.process.poll() is None:
                    logger.info(f"Bot started successfully (PID: {self.process.pid})")
                    return True
                else:
                    logger.error(f"Bot process exited prematurely with code: {self.process.returncode}")
                    return False
            else:
                try:
                    # Launch the service process in the background
                    self.process = subprocess.Popen(['bash', self.command_path], 
                                               stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL,
                                               preexec_fn=os.setsid if os.name != 'nt' else None)
                    
                    # Track this process globally
                    child_processes.add(self.process.pid)
                    
                    logger.info(f"Started {self.name} process with command: {self.command_path} (PID: {self.process.pid})")
                    
                    # Wait for service to become available
                    start_time = time.time()
                    while time.time() - start_time < self.startup_timeout:
                        # First check if the process has terminated
                        if self.process.poll() is not None:
                            logger.error(f"{self.name} process exited prematurely with code: {self.process.returncode}")
                            return False
                            
                        # Then check if the service endpoint is responding
                        async with aiohttp.ClientSession() as session:
                            if await self.is_running(session):
                                logger.info(f"{self.name} is now running")
                                return True
                        logger.info(f"Waiting for {self.name} to start...")
                        await asyncio.sleep(2)
                    
                    logger.error(f"{self.name} did not start within {self.startup_timeout} seconds")
                    return False
                except Exception as e:
                    logger.error(f"Failed to start {self.name}: {e}")
                    return False
            
        except Exception as e:
            logger.error(f"Failed to start {self.name}: {e}")
            return False

services = [
    Startup(
        name="API", 
        host=API_HOST, 
        port=API_PORT, 
        endpoint=API_ENDPOINT,
        command_path=os.path.join(DOMESTIC_AI_PATH, "domestic-api", "run-api.command")
    ),
    Startup(
        name="Bot",
        port=None,
        endpoint="/",
        command_path=os.path.join(DOMESTIC_AI_PATH, "domestic-bot", "run-bot.command")
    ),
    Startup(
        name="Rembg Tool", 
        port=8008, 
        endpoint="/",
        command_path=os.path.join(DOMESTIC_AI_PATH, "domestic-tools", "domestic-rembg", "run-rembg.command")
    ),
    Startup(
        name="Image Generation Tool", 
        port=8042, 
        endpoint="/queue-status",
        command_path=os.path.join(DOMESTIC_AI_PATH, "domestic-tools", "domestic-imagen", "run-imagen.command")
    )
]

async def ensure_service_running(service: Startup, max_attempts: int = 5) -> bool:
    """Ensure a service is running, attempting to start it if needed"""
    global bot_process, child_processes

    if service.name == "Bot":
        # If we already have a bot process and it's still running
        if bot_process is not None and bot_process.poll() is None:
            logger.info(f"Bot is already running (PID: {bot_process.pid})")
            return True
            
        # Otherwise try to start it
        logger.info("Bot not running, starting it...")
        return await service.start()
    
    # For other services with endpoints
    for attempt in range(max_attempts):
        # First check if it's already running
        async with aiohttp.ClientSession() as session:
            try:
                if await service.is_running(session):
                    logger.info(f"{service.name} is already running")
                    return True
            except Exception as e:
                logger.warning(f"Error checking if {service.name} is running: {e}")
        
        if attempt < max_attempts - 1:
            logger.info(f"{service.name} not available (attempt {attempt+1}/{max_attempts}), waiting...")
            await asyncio.sleep(2)
    
    # Service is not running, try to start it
    logger.info(f"{service.name} not available after {max_attempts} attempts, trying to start it")
    
    # Try to stop any existing instance first to avoid port conflicts
    if service.port is not None:
        process = find_process_by_port(service.port)
        if process:
            logger.warning(f"Process already using port {service.port} (PID: {process.pid}), stopping it first")
            try:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except psutil.TimeoutExpired:
                    process.kill()
            except Exception as e:
                logger.error(f"Error stopping existing process: {e}")
    
    # Now try to start the service
    return await service.start()

async def ensure_services_running(services: List[Startup]) -> Dict[str, bool]:
    """Ensure multiple services are running and return their status"""
    results = {}
    for service in services:
        results[service.name] = await ensure_service_running(service)
    return results

def find_process_by_port(port: int) -> Optional[psutil.Process]:
    """Find a process that is listening on the given port"""
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                # Get connections separately for each process
                connections = proc.net_connections(kind='inet')
                for conn in connections:
                    if conn.laddr.port == port:
                        return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception as e:
        logger.error(f"Error in find_process_by_port: {e}")
        return None
    return None

def get_child_processes(pid: int) -> Set[int]:
    """Recursively get all child processes of a given PID"""
    try:
        parent = psutil.Process(pid)
        children = set()
        
        # Get direct children
        for child in parent.children(recursive=False):
            children.add(child.pid)
            # Get their children recursively
            children.update(get_child_processes(child.pid))
            
        return children
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return set()

def kill_process_tree(pid: int) -> bool:
    """Kill a process and all its children"""
    try:
        # Get the process group ID on Unix systems
        if os.name != 'nt':
            try:
                # Try to kill the entire process group
                os.killpg(os.getpgid(pid), signal.SIGKILL)
                logger.info(f"Killed process group for PID {pid}")
                return True
            except (ProcessLookupError, PermissionError) as e:
                logger.warning(f"Failed to kill process group for PID {pid}: {e}")
                # Fall back to individual process killing
        
        # Get all child processes
        children = get_child_processes(pid)
        children.add(pid)  # Include the parent
        
        # Kill all processes
        for p_id in children:
            try:
                proc = psutil.Process(p_id)
                proc.kill()
                logger.info(f"Killed process with PID {p_id}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
                
        return True
    except Exception as e:
        logger.error(f"Error killing process tree for PID {pid}: {e}")
        return False

async def stop_all_services() -> bool:
    """Stop all services (API and tools) using tracked PIDs"""
    global child_processes
    logger.info(f"Stopping all services with tracked PIDs: {child_processes}")
    
    # First, try to kill via process groups (more effective for child processes)
    success = True
    for pid in list(child_processes):
        try:
            success = kill_process_tree(pid) and success
        except Exception as e:
            logger.error(f"Error stopping process tree for PID {pid}: {e}")
            success = False
    
    # Additional fallback - look for any process with DOMESTIC_AI_PATH in its command line
    path = DOMESTIC_AI_PATH
    killed = []
    try:
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and any(path in cmd for cmd in cmdline):
                    logger.info(f"Killing untracked process: PID {proc.pid}, cmdline: {' '.join(cmdline[:2])}")
                    proc.kill()
                    killed.append(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception as e:
        logger.error(f"Error during fallback process killing: {e}")
    
    total_killed = len(killed)
    logger.info(f"Killed {total_killed} additional processes: {killed}")
    
    # Also kill any Python processes running in DOMESTIC_AI_PATH
    try:
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                # Check if it's a Python process
                if 'python' in proc.name().lower():
                    try:
                        cmdline = proc.cmdline()
                        # Check if command line contains our path
                        if any(path in cmd for cmd in cmdline):
                            logger.info(f"Killing Python process: PID {proc.pid}, cmdline: {' '.join(cmdline[:2])}")
                            proc.kill()
                            killed.append(proc.pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as e:
        logger.error(f"Error killing Python processes: {e}")
    
    new_killed = len(killed) - total_killed
    logger.info(f"Killed {new_killed} Python processes")
    
    return success or len(killed) > 0

async def wait_for_api():
    """Replacement for the original wait_for_api function"""
    api_service = next(service for service in services if service.name == "API")
    logger.info(f"Waiting for API: {api_service}")
    return await ensure_service_running(api_service)

async def start_bot():
    """Replacement for the original start_bot function"""
    bot_service = next(service for service in services if service.name == "Bot")
    logger.info(f"Starting bot: {bot_service}")
    await ensure_service_running(bot_service)

async def start_tools():
    """Replacement for the original start_tools function"""
    tool_services = [service for service in services if service.name != "API"]
    results = await ensure_services_running(tool_services)
    logger.info(f"Started tools: {results}")
    return all(results.values())

async def ensure_all_services():
    """Ensure all services (API and tools) are running"""
    results = await ensure_services_running(services)
    logger.info(f"Ensured all services: {results}")
    return all(results.values())

# Add helper function to verify all processes were terminated
async def verify_shutdown():
    """Verify that all processes have been shut down"""
    path = DOMESTIC_AI_PATH
    running = []
    
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = proc.cmdline()
            if cmdline and any(path in cmd for cmd in cmdline):
                running.append((proc.pid, ' '.join(cmdline[:2])))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    if running:
        logger.error(f"Found {len(running)} processes still running after shutdown:")
        for pid, cmd in running:
            logger.error(f"  PID {pid}: {cmd}")
        return False
    else:
        logger.info("All processes successfully terminated")
        return True