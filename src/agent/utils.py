import os
import psutil
import docker
import cryptography
import time

class System:    
    @staticmethod
    def get_cpu_info():
        cpu_info = {
            "physical_cores": psutil.cpu_count(logical=False),
            "total_cores": psutil.cpu_count(logical=True),
            "max_frequency": psutil.cpu_freq().max,
            "min_frequency": psutil.cpu_freq().min,
            "current_frequency": psutil.cpu_freq().current,
            "cpu_usage_per_core": psutil.cpu_percent(percpu=True),
            "total_cpu_usage": psutil.cpu_percent(),
            "cpu_load_average": psutil.getloadavg(),
            "cpu_temperature": psutil.sensors_temperatures().get('coretemp', [])
        }
        return cpu_info
    
    @staticmethod
    def get_cpu_curr_freq():
        return psutil.cpu_freq().current
    
    @staticmethod
    def get_cpu_utilization():
        total = psutil.cpu_percent(interval = 1, percpu = False)
        divided = psutil.cpu_times_percent()
        return {
            "total": total,
            "user": divided.user,
            "system": divided.system,
            "idle": divided.idle,
            "iowait": divided.iowait
        }
        
    @staticmethod
    def get_memory_info():
        virtual_memory = psutil.virtual_memory()
        swap_memory = psutil.swap_memory()
        memory_info = {
            "virtual_memory": {
                "total": virtual_memory.total,
                "available": virtual_memory.available,
                "used": virtual_memory.used,
                "free": virtual_memory.free,
                "percent": virtual_memory.percent
            },
            "swap_memory": {
                "total": swap_memory.total,
                "used": swap_memory.used,
                "free": swap_memory.free,
                "percent": swap_memory.percent
            }
        }
        return memory_info
    
    @staticmethod
    def get_storage_info():
        usage = psutil.disk_usage('/')
        diskio = psutil.disk_io_counters()
        storage_info = {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.percent,
        }
        return storage_info
    
    @staticmethod
    def get_uptime():
        return time.time() - psutil.boot_time()
    