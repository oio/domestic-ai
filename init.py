import asyncio
import logging
import sys
import signal
import init_functions as startup
import os
import time
import psutil

logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
	handlers=[
		logging.StreamHandler(sys.stdout)
	]
)

logger = logging.getLogger("main")

shutdown_in_progress = False

async def initialize_services():
	logger.info("Starting domestic-ai initialization...")
	
	success = await startup.ensure_all_services()
	
	if not success:
		logger.critical("Some services failed to start - cannot continue")
		return False
	
	logger.info("Initialization complete - system is now running")
	return True

async def forceful_kill_processes():
	path = os.environ.get('DOMESTIC_AI_PATH', '.')
	killed = []
	
	logger.info(f"Forcefully killing any remaining processes under {path}")
	
	try:
		for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
			try:
				if 'python' in proc.name().lower():
					cmdline = proc.cmdline()
					if cmdline and any(path in cmd for cmd in cmdline):
						logger.info(f"Killing Python process {proc.pid}: {' '.join(cmdline[:2])}...")
						try:
							if os.name != 'nt':
								os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
								logger.info(f"Killed process group for {proc.pid}")
							else:
								proc.kill()
							killed.append(proc.pid)
						except Exception as e:
							logger.error(f"Failed to kill process {proc.pid}: {e}")
							try:
								proc.kill()
								killed.append(proc.pid)
							except Exception as e2:
								logger.error(f"Failed to directly kill process {proc.pid}: {e2}")
			except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
				pass
	except Exception as e:
		logger.error(f"Error during forceful Python process kill: {e}")
	
	try:
		if os.name != 'nt':
			ps_cmd = f"ps aux | grep python | grep '{path}' | awk '{{print $2}}'"
			logger.info(f"Running system command: {ps_cmd}")
			
			result = os.popen(ps_cmd).read().strip()
			if result:
				pids = result.split('\n')
				for pid_str in pids:
					try:
						pid = int(pid_str)
						os.system(f"kill -9 -{os.getpgid(pid)} 2>/dev/null")
						logger.info(f"Killed process group for PID {pid} via system command")
						killed.append(pid)
					except (ValueError, ProcessLookupError) as e:
						logger.error(f"Error killing PID {pid_str}: {e}")
	except Exception as e:
		logger.error(f"Error during system command kill: {e}")
	
	try:
		for port in [8000, 8008, 8042]:
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
	path = os.environ.get('DOMESTIC_AI_PATH', '.')
	running = []
	
	for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
		try:
			if 'python' in proc.name().lower():
				cmdline = proc.cmdline()
				if cmdline and any(path in cmd for cmd in cmdline):
					running.append((proc.pid, ' '.join(cmdline[:2])))
		except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
			pass
	
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
	global shutdown_in_progress
	
	if shutdown_in_progress:
		logger.info("Shutdown already in progress, skipping")
		return
	
	shutdown_in_progress = True
	logger.info("Shutting down all services...")
	
	try:
		signal_file = os.path.join(os.environ.get('DOMESTIC_AI_PATH', '.'), "bot_shutdown.signal")
		with open(signal_file, 'w') as f:
			f.write(str(time.time()))
		
		logger.info("Created shutdown signal file")
		
		await asyncio.sleep(2)
		
		logger.info("Stopping all services with graceful shutdown...")
		await startup.stop_all_services()
		
		await asyncio.sleep(1)
		
		await forceful_kill_processes()
		
		if not await verify_shutdown():
			logger.warning("Some processes still running, trying more aggressive shutdown...")
			await forceful_kill_processes()
			
			if not await verify_shutdown():
				logger.error("Failed to kill all processes, some may still be running")
		
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
		os._exit(0)

def handle_signals():
	loop = asyncio.get_event_loop()
	
	def signal_handler(sig_name):
		logger.info(f"{sig_name} signal received")
		loop.create_task(shutdown())
	
	for sig, name in [(signal.SIGINT, "SIGINT"), (signal.SIGTERM, "SIGTERM")]:
		loop.add_signal_handler(sig, lambda s=name: signal_handler(s))
	
	logger.info("Signal handlers registered for SIGINT and SIGTERM")

async def main():
	try:
		handle_signals()
		
		success = await initialize_services()
		if not success:
			logger.error("Initialization failed")
			await shutdown()
			return
		
		done = asyncio.Event()
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
		print("\nKeyboard interrupt caught outside event loop, performing emergency shutdown...")
		try:
			path = os.environ.get('DOMESTIC_AI_PATH', '.')
			if os.name != 'nt':
				os.system(f"ps aux | grep python | grep '{path}' | awk '{{print $2}}' | xargs -I{{}} kill -9 {{}} 2>/dev/null")
				print("Used system command to kill Python processes")
			
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