import aiohttp
import asyncio
import dotenv
import logging
import os
import psutil
import subprocess
import time
from typing import Dict, List, Optional
dotenv.load_dotenv()
logger = logging.getLogger('discord')

# Constants
STARTUP_TIMEOUT = 60
DOMESTIC_AI_PATH = os.environ['DOMESTIC_AI_PATH']
API_HOST = "0.0.0.0"
API_PORT = 8000
API_ENDPOINT = "/api_endpoints"
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

	async def is_running(self, session: aiohttp.ClientSession, timeout: int = 2) -> bool:
		"""Check if the service is running"""
		try:
			async with session.get(self.url, timeout=timeout) as response:
				return response.status == 200
		except (aiohttp.ClientError, asyncio.TimeoutError):
			return False

	async def start(self) -> bool:
		"""Start the service if it's not already running"""
		
		if not self.command_path:
			logger.warning(f"No command path specified for {self.name}, cannot start")
			return False

		try:
			# Special handling for the Bot
			if self.name == "Bot":
				global bot_process  # Use the global variabl
				logger.info(f"Starting {self.name} process with command: {self.command_path}")
				# Launch the bot process and store its reference
				bot_process = subprocess.Popen(['bash', self.command_path], 
											 stdout=subprocess.DEVNULL,
											 stderr=subprocess.DEVNULL,
											 start_new_session=True)
				await asyncio.sleep(5)  # Give it time to start
				
				# Check if process is still running
				if bot_process.poll() is None:
					logger.info(f"Bot started successfully (PID: {bot_process.pid})")
					return True
				else:
					logger.error(f"Bot process exited prematurely with code: {bot_process.returncode}")
					return False
			else:
				try:
					# Launch the service process in the background
					subprocess.Popen(['bash', self.command_path], 
									stdout=subprocess.DEVNULL,
									stderr=subprocess.DEVNULL,
									start_new_session=True)
					
					logger.info(f"Started {self.name} process with command: {self.command_path}")
					
					# Wait for service to become available
					start_time = time.time()
					while time.time() - start_time < self.startup_timeout:
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

	async def stop(self) -> bool:
		"""Stop the service if it's running"""
		global bot_process  # Use the global variable
		
		if self.name == "Bot":
			if bot_process is not None:
				try:
					logger.info(f"Stopping bot process (PID: {bot_process.pid})")
				
					# First try to create a file to signal the bot to disconnect gracefully
					signal_file = os.path.join(DOMESTIC_AI_PATH, "bot_shutdown.signal")
					with open(signal_file, 'w') as f:
						f.write(str(time.time()))
					
					# Give the bot a few seconds to handle the signal file
					await asyncio.sleep(3)
					
					# Then try to terminate gracefully
					bot_process.terminate()
					
					# Wait for process to terminate (with timeout)
					try:
						bot_process.wait(timeout=5)
					except subprocess.TimeoutExpired:
						logger.warning("Bot didn't terminate gracefully, forcing kill")
						bot_process.kill()
					
					# Clean up signal file
					if os.path.exists(signal_file):
						os.remove(signal_file)
					
					logger.info("Successfully stopped Bot")
					bot_process = None  # Clear the global reference
					return True
				except Exception as e:
					logger.error(f"Error stopping Bot: {e}")
					return False
			else:
				# Try to find the bot process by looking for the command
				for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
					try:
						cmdline = proc.cmdline()
						if any("run-bot.command" in cmd for cmd in cmdline):
							logger.info(f"Found Bot process (PID: {proc.pid}), stopping it")
							proc.terminate()
							try:
								proc.wait(timeout=5)
							except psutil.TimeoutExpired:
								logger.warning("Bot didn't terminate gracefully, forcing kill")
								proc.kill()
							logger.info("Successfully stopped Bot")
							return True
					except (psutil.NoSuchProcess, psutil.AccessDenied):
						continue
			
			logger.warning("No Bot process found to stop")
			return True
			
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
		endpoint="/queue-status",  # Changed from "/" to a real endpoint
		command_path=os.path.join(DOMESTIC_AI_PATH, "domestic-tools", "domestic-imagen", "run-imagen.command")
	)
]

async def ensure_service_running(service: Startup, max_attempts: int = 5) -> bool:
	"""Ensure a service is running, attempting to start it if needed"""
	
	global bot_process

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
		for proc in psutil.process_iter(['pid', 'name']):  # Remove 'connections' from here
			try:
				# Get connections separately for each process
				connections = proc.connections(kind='inet')
				for conn in connections:
					if conn.laddr.port == port:
						return proc
			except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
				pass
	except Exception as e:
		logger.error(f"Error in find_process_by_port: {e}")
		return None
	return None

async def stop_service(service: Startup) -> bool:
	"""Stop a specific service"""
	logger.info(f"Stopping service: {service.name}")
	if service.name == "Bot":
		if bot_process is not None:
			try:
				logger.info(f"Stopping bot process (PID: {bot_process.pid})")
			
				# First try to create a file to signal the bot to disconnect gracefully
				signal_file = os.path.join(DOMESTIC_AI_PATH, "bot_shutdown.signal")
				with open(signal_file, 'w') as f:
					f.write(str(time.time()))
				
				# Give the bot a few seconds to handle the signal file
				await asyncio.sleep(3)
				
				# Check if process is still running
				if bot_process.poll() is None:
					# Then try to send a termination signal directly
					try:
						import signal
						os.kill(bot_process.pid, signal.SIGTERM)
						logger.info(f"Sent SIGTERM to bot process (PID: {bot_process.pid})")
					except Exception as e:
						logger.error(f"Failed to send SIGTERM: {e}")
				
					# Wait for process to terminate (with timeout)
					try:
						bot_process.wait(timeout=5)
					except subprocess.TimeoutExpired:
						logger.warning("Bot didn't terminate gracefully, forcing kill")
						bot_process.kill()
				else:
					logger.info("Bot process has already terminated")
				
				# Clean up signal file
				if os.path.exists(signal_file):
					os.remove(signal_file)
				
				logger.info("Successfully stopped Bot")
				bot_process = None  # Clear the global reference
				return True
			except Exception as e:
				logger.error(f"Error stopping Bot: {e}")
				return False
		else:
			# More aggressive bot process finding and termination
			try:
				# Try to find the bot process by looking for python processes that contain 'bot.py'
				found_process = False
				for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
					try:
						cmdline = proc.cmdline()
						if any("python" in cmd.lower() for cmd in cmdline) and any("bot.py" in cmd for cmd in cmdline):
							logger.info(f"Found Bot process (PID: {proc.pid}), stopping it")
							proc.terminate()
							found_process = True
							try:
								proc.wait(timeout=5)
							except psutil.TimeoutExpired:
								logger.warning("Bot didn't terminate gracefully, forcing kill")
								proc.kill()
							logger.info("Successfully stopped Bot")
					except (psutil.NoSuchProcess, psutil.AccessDenied):
						continue
				
				# Also look for the run-bot.command process
				for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
					try:
						cmdline = proc.cmdline()
						if any("run-bot.command" in cmd for cmd in cmdline):
							logger.info(f"Found run-bot.command process (PID: {proc.pid}), stopping it")
							proc.terminate()
							found_process = True
							try:
								proc.wait(timeout=5)
							except psutil.TimeoutExpired:
								logger.warning("run-bot.command didn't terminate gracefully, forcing kill")
								proc.kill()
							logger.info("Successfully stopped run-bot.command")
					except (psutil.NoSuchProcess, psutil.AccessDenied):
						continue
				
				# Create the signal file anyway in case we missed the process
				signal_file = os.path.join(DOMESTIC_AI_PATH, "bot_shutdown.signal")
				with open(signal_file, 'w') as f:
					f.write(str(time.time()))
				
				await asyncio.sleep(3)  # Give a chance for any bot to detect the signal
				
				# Clean up signal file
				if os.path.exists(signal_file):
					try:
						os.remove(signal_file)
					except:
						pass
				
				if not found_process:
					logger.warning("No Bot process found to stop")
				return True
			except Exception as e:
				logger.error(f"Error finding and stopping Bot: {e}")
				return False
	else:
		return await service.stop()

async def stop_services(services: List[Startup]) -> Dict[str, bool]:
	"""Stop multiple services and return their status"""
	results = {}
	for service in services:
		results[service.name] = await stop_service(service)
	return results

async def stop_all_services() -> bool:
	"""Stop all services (API and tools)"""
	logger.info("Stopping all services...")
	results = await stop_services(services)
	return all(results.values())

# New function to stop specific service types
async def stop_api():
	"""Stop just the API"""
	api_service = next(service for service in services if service.name == "API")
	return await stop_service(api_service)

async def stop_tools():
	"""Stop just the tools"""
	tool_services = [service for service in services if service.name != "API"]
	results = await stop_services(tool_services)
	return all(results.values())

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

async def check_service_health(service: Startup) -> bool:
	"""
	Enhanced health check for a service that goes beyond simple connectivity
	"""
	if service.name == "Bot":
		# Bot doesn't have an HTTP endpoint, so we'll check if the process is running
		for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
			try:
				if "run-bot.command" in " ".join(proc.cmdline()):
					return True
			except (psutil.NoSuchProcess, psutil.AccessDenied):
				continue
		return False
	
	# For other services, check their HTTP endpoint
	async with aiohttp.ClientSession() as session:
		try:
			# Try multiple times with increasing timeouts
			for timeout in [1, 2, 3]:
				try:
					async with session.get(service.url, timeout=timeout) as response:
						if response.status == 200:
							return True
				except asyncio.TimeoutError:
					continue
				except aiohttp.ClientError:
					# If it's not a timeout but a connection error, break immediately
					break
			
			return False
		except Exception as e:
			logger.error(f"Error checking health for {service.name}: {e}")
			return False

async def robust_service_start(service: Startup, retry_delay: int = 2) -> bool:
	"""
	More robust version of service startup with better error handling
	"""
	if not service.command_path:
		logger.warning(f"No command path specified for {service.name}, cannot start")
		return False
	
	# Check if the service is already running first
	async with aiohttp.ClientSession() as session:
		if await service.is_running(session):
			logger.info(f"{service.name} is already running")
			return True
	
	# Stop any conflicting process using the same port first (except Bot)
	if service.name != "Bot" and service.port is not None:
		process = find_process_by_port(service.port)
		if process:
			logger.warning(f"Process already using port {service.port} (PID: {process.pid}), stopping it")
			try:
				process.terminate()
				try:
					process.wait(timeout=5)
				except psutil.TimeoutExpired:
					process.kill()
			except psutil.NoSuchProcess:
				pass
	
	# Try to start the service
	try:
		# Launch the service process in the background with better handling
		logger.info(f"Starting {service.name} with command: {service.command_path}")
		
		# Execute the command with proper environment
		env = os.environ.copy()
		process = subprocess.Popen(['bash', service.command_path], 
								  stdout=subprocess.PIPE,
								  stderr=subprocess.PIPE,
								  env=env,
								  start_new_session=True)
		
		# For Bot, we assume success after a delay
		if service.name == "Bot":
			await asyncio.sleep(5)
			# Check if process is still running
			if process.poll() is None:
				logger.info(f"Bot started successfully")
				return True
			else:
				stdout, stderr = process.communicate()
				logger.error(f"Bot failed to start: {stderr.decode('utf-8', errors='ignore')}")
				return False
		
		# For other services, wait until the endpoint becomes available
		start_time = time.time()
		while time.time() - start_time < service.startup_timeout:
			async with aiohttp.ClientSession() as session:
				if await service.is_running(session):
					logger.info(f"{service.name} is now running")
					return True
			
			# Check if process has terminated with error
			if process.poll() is not None:
				stdout, stderr = process.communicate()
				logger.error(f"{service.name} process exited with code {process.returncode}")
				logger.error(f"Error output: {stderr.decode('utf-8', errors='ignore')}")
				return False
				
			logger.info(f"Waiting for {service.name} to start... ({int(time.time() - start_time)}s)")
			await asyncio.sleep(retry_delay)
		
		logger.error(f"{service.name} did not start within {service.startup_timeout} seconds")
		return False
		
	except Exception as e:
		logger.error(f"Failed to start {service.name}: {e}")
		import traceback
		logger.error(traceback.format_exc())
		return False
	
# Add this to init_functions.py or replace the existing robust_service_start method

async def robust_service_start(service: Startup, retry_delay: int = 2) -> bool:
	"""
	More robust version of service startup with better error handling and logging
	"""
	if not service.command_path:
		logger.warning(f"No command path specified for {service.name}, cannot start")
		return False
	
	# Check if the service is already running first
	try:
		async with aiohttp.ClientSession() as session:
			if await service.is_running(session):
				logger.info(f"{service.name} is already running")
				return True
	except Exception as e:
		logger.warning(f"Error checking if {service.name} is running: {e}")
	
	# Check if the command path exists
	if not os.path.exists(service.command_path):
		logger.error(f"Command path does not exist: {service.command_path}")
		return False
	
	# Check if the command is executable
	if not os.access(service.command_path, os.X_OK):
		logger.warning(f"Command is not executable: {service.command_path}")
		try:
			os.chmod(service.command_path, 0o755)  # Try to make it executable
			logger.info(f"Made command executable: {service.command_path}")
		except Exception as e:
			logger.error(f"Failed to make command executable: {e}")
			return False
	
	# Stop any conflicting process using the same port first (except Bot)
	if service.name != "Bot" and service.port is not None:
		process = find_process_by_port(service.port)
		if process:
			logger.warning(f"Process already using port {service.port} (PID: {process.pid}), stopping it")
			try:
				process.terminate()
				try:
					process.wait(timeout=5)
				except psutil.TimeoutExpired:
					process.kill()
			except psutil.NoSuchProcess:
				pass
	
	# Try to start the service
	try:
		# Launch the service process in the background with output capture
		logger.info(f"Starting {service.name} with command: {service.command_path}")
		
		# Execute the command with proper environment
		env = os.environ.copy()
		
		# Create temporary files for stdout and stderr
		stdout_file = os.path.join(DOMESTIC_AI_PATH, f"{service.name.lower()}_stdout.log")
		stderr_file = os.path.join(DOMESTIC_AI_PATH, f"{service.name.lower()}_stderr.log")
		
		with open(stdout_file, 'w') as out, open(stderr_file, 'w') as err:
			process = subprocess.Popen(['bash', service.command_path], 
									stdout=out,
									stderr=err,
									env=env,
									start_new_session=True)
		
		# For Bot, we assume success after a delay
		if service.name == "Bot":
			await asyncio.sleep(5)
			# Check if process is still running
			if process.poll() is None:
				logger.info(f"Bot started successfully")
				return True
			else:
				# Read and log the output files
				with open(stdout_file, 'r') as f:
					stdout = f.read()
				with open(stderr_file, 'r') as f:
					stderr = f.read()
				
				logger.error(f"Bot failed to start. Return code: {process.returncode}")
				logger.error(f"stdout: {stdout[:500]}...")
				logger.error(f"stderr: {stderr[:500]}...")
				return False
		
		# For other services, wait until the endpoint becomes available
		start_time = time.time()
		while time.time() - start_time < service.startup_timeout:
			async with aiohttp.ClientSession() as session:
				if await service.is_running(session):
					logger.info(f"{service.name} is now running")
					return True
			
			# Check if process has terminated with error
			if process.poll() is not None:
				# Read and log the output files
				with open(stdout_file, 'r') as f:
					stdout = f.read()
				with open(stderr_file, 'r') as f:
					stderr = f.read()
				
				logger.error(f"{service.name} process exited with code {process.returncode}")
				logger.error(f"stdout: {stdout[:500]}...")
				logger.error(f"stderr: {stderr[:500]}...")
				return False
				
			logger.info(f"Waiting for {service.name} to start... ({int(time.time() - start_time)}s)")
			await asyncio.sleep(retry_delay)
		
		logger.error(f"{service.name} did not start within {service.startup_timeout} seconds")
		
		# Try to get logs even for timeout
		with open(stdout_file, 'r') as f:
			stdout = f.read()
		with open(stderr_file, 'r') as f:
			stderr = f.read()
		
		logger.error(f"Last stdout: {stdout[-500:] if stdout else 'No output'}")
		logger.error(f"Last stderr: {stderr[-500:] if stderr else 'No error output'}")
		
		return False
		
	except Exception as e:
		logger.error(f"Failed to start {service.name}: {e}")
		import traceback
		logger.error(traceback.format_exc())
		return False