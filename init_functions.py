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

STARTUP_TIMEOUT = 60
DOMESTIC_AI_PATH = os.environ['DOMESTIC_AI_PATH']
API_HOST = "0.0.0.0"
API_PORT = 8000
API_ENDPOINT = "/api_endpoints"
child_processes = set()
bot_process = None

class Startup:
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
		self.process = None  

	async def is_running(self, session: aiohttp.ClientSession, timeout: int = 2) -> bool:
		if self.port is None:
			return self.process is not None and self.process.poll() is None
			
		try:
			async with session.get(self.url, timeout=timeout) as response:
				return response.status == 200
		except (aiohttp.ClientError, asyncio.TimeoutError):
			return False

	async def start(self) -> bool:
		global bot_process, child_processes
		
		if not self.command_path:
			logger.warning(f"No command path specified for {self.name}, cannot start")
			return False

		try:
			if self.name == "Bot":
				logger.info(f"Starting {self.name} process with command: {self.command_path}")
				self.process = subprocess.Popen(['bash', self.command_path], 
											 stdout=subprocess.DEVNULL,
											 stderr=subprocess.DEVNULL,
											 preexec_fn=os.setsid if os.name != 'nt' else None)
				
				bot_process = self.process
				child_processes.add(self.process.pid)
				
				await asyncio.sleep(1)
				
				if self.process.poll() is None:
					logger.info(f"Bot started successfully (PID: {self.process.pid})")
					return True
				else:
					logger.error(f"Bot process exited prematurely with code: {self.process.returncode}")
					return False
			else:
				try:
					self.process = subprocess.Popen(['bash', self.command_path], 
											   stdout=subprocess.DEVNULL,
											   stderr=subprocess.DEVNULL,
											   preexec_fn=os.setsid if os.name != 'nt' else None)
					
					child_processes.add(self.process.pid)
					
					logger.info(f"Started {self.name} process with command: {self.command_path} (PID: {self.process.pid})")
					
					start_time = time.time()
					while time.time() - start_time < self.startup_timeout:
						if self.process.poll() is not None:
							logger.error(f"{self.name} process exited prematurely with code: {self.process.returncode}")
							return False
							
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

# Edit this to add or remove services
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

async def ensure_service_running(service: Startup, max_attempts: int = 1) -> bool:
	global bot_process, child_processes

	if service.name == "Bot":
		if bot_process is not None and bot_process.poll() is None:
			logger.info(f"Bot is already running (PID: {bot_process.pid})")
			return True
			
		logger.info("Bot not running, starting it...")
		return await service.start()
	
	for attempt in range(max_attempts):
		async with aiohttp.ClientSession() as session:
			try:
				if await service.is_running(session):
					logger.info(f"{service.name} is already running")
					return True
			except Exception as e:
				logger.warning(f"Error checking if {service.name} is running: {e}")
		
		if attempt < max_attempts - 1:
			logger.info(f"{service.name} not available (attempt {attempt+1}/{max_attempts}), waiting...")
			await asyncio.sleep(1)
	
	logger.info(f"{service.name} not available after {max_attempts} attempts, trying to start it")
	
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
	
	return await service.start()

async def ensure_services_running(services: List[Startup]) -> Dict[str, bool]:
	results = {}
	for service in services:
		results[service.name] = await ensure_service_running(service)
	return results

def find_process_by_port(port: int) -> Optional[psutil.Process]:
	try:
		for proc in psutil.process_iter(['pid', 'name']):
			try:
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
	try:
		parent = psutil.Process(pid)
		children = set()
		
		for child in parent.children(recursive=False):
			children.add(child.pid)
			children.update(get_child_processes(child.pid))
			
		return children
	except (psutil.NoSuchProcess, psutil.AccessDenied):
		return set()

def kill_process_tree(pid: int) -> bool:
	try:
		if os.name != 'nt':
			try:
				os.killpg(os.getpgid(pid), signal.SIGKILL)
				logger.info(f"Killed process group for PID {pid}")
				return True
			except (ProcessLookupError, PermissionError) as e:
				logger.warning(f"Failed to kill process group for PID {pid}: {e}")
		
		children = get_child_processes(pid)
		children.add(pid)
		
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
	global child_processes
	logger.info(f"Stopping all services with tracked PIDs: {child_processes}")
	
	success = True
	for pid in list(child_processes):
		try:
			success = kill_process_tree(pid) and success
		except Exception as e:
			logger.error(f"Error stopping process tree for PID {pid}: {e}")
			success = False
	
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
	
	try:
		for proc in psutil.process_iter(['pid', 'name', 'exe']):
			try:
				if 'python' in proc.name().lower():
					try:
						cmdline = proc.cmdline()
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

async def ensure_all_services():
	results = await ensure_services_running(services)
	logger.info(f"Ensured all services: {results}")
	return all(results.values())

async def verify_shutdown():
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