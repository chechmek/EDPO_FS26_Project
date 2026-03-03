import psutil
import time

def monitor_system(duration=10):
    cpu = []
    memory = []

    start = time.time()

    while time.time() - start < duration:
        cpu.append(psutil.cpu_percent())
        memory.append(psutil.virtual_memory().percent)
        time.sleep(1)

    return sum(cpu)/len(cpu), sum(memory)/len(memory)