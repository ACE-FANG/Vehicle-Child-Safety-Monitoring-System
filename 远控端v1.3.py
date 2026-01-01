# remote_control.py - 车载儿童安全监控系统远程控制端（界面布局优化版）
"""
车载儿童安全监控系统 - 远程控制端（界面布局优化版）
优化内容：
1. 重新设计仪表盘布局，解决右侧空白问题
2. 优化组件排列，提升视觉效果
3. 改进图片显示区域
4. 实现风险状态与报警记录同步
作者：方钦炯
日期：2025年12月1日
版本：v0.1.2.3（界面布局优化版）
"""
import sys
import json
import time
import threading
import traceback
import queue
import os
import base64
from datetime import datetime, timedelta
import sqlite3
import pandas as pd
import numpy as np
import uuid
from PyQt5.QtCore import QTimer, QMetaObject, Qt, Q_ARG, pyqtSignal, QObject, QThread

# 全局异常处理
def global_exception_handler(exc_type, exc_value, exc_traceback):
    """处理未捕获的异常"""
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print("=" * 80)
    print("程序发生未捕获异常:")
    print(error_msg)
    print("=" * 80)
    # 保存到错误日志
    try:
        with open("remote_control_error.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] 程序异常:\n{error_msg}\n")
    except:
        pass
    # 使用标准错误输出
    sys.stderr.write(f"程序异常退出: {exc_type.__name__}: {exc_value}\n")

# 设置全局异常处理
sys.excepthook = global_exception_handler

# 第三方库导入
try:
    import paho.mqtt.client as mqtt
    from PyQt5.QtWidgets import *
    from PyQt5.QtCore import *
    from PyQt5.QtGui import *
    from PyQt5.QtChart import QChart, QChartView, QLineSeries, QValueAxis, QDateTimeAxis
    import pyqtgraph as pg
    import requests
    print("所有依赖库导入成功")
except ImportError as e:
    print(f"缺少依赖库: {e}")
    print("请安装: pip install paho-mqtt pandas numpy pyqt5 pyqtgraph requests")
    sys.exit(1)

# ==================== 配置类 ====================
class RemoteConfig:
    """远程控制端配置"""
    # MQTT配置
    MQTT_BROKER = "broker.emqx.io"
    MQTT_PORT = 1883
    MQTT_USER = ""
    MQTT_PASSWORD = ""
    # 订阅和发布主题
    MQTT_TOPICS = {
        "status": "esp32/main/status",           # 订阅：设备状态
        "child_detection": "esp32cam/child_detection",  # 订阅：儿童检测
        "control": "python/control",             # 发布：控制命令
        "sensor_data": "python/sensor_data",     # 订阅：设备端传感器数据
        "alerts": "python/alerts",                # 新增：订阅设备端报警同步
        "captured_image": "python/captured_image", # 新增：订阅抓拍图片
    }
    # 远程访问配置
    DEVICE_ID = "vehicle_monitor_001"
    REMOTE_CONTROL_PASSWORD = "admin123"  # 远程控制密码
    # 数据存储
    DB_PATH = "remote_monitor.db"
    # 报警设置（仅用于风险评估，不生成报警）
    ALERT_THRESHOLDS = {
        "temperature_high": 35.0,
        "temperature_extreme": 40.0,
        "co2_high": 1000,
        "co2_extreme": 1500,
        "tvoc_high": 500,
        "tvoc_extreme": 1000
    }
    # 图片保存配置
    CAPTURED_IMAGE_DIR = "captured_images"  # 图片保存目录

# ==================== 数据模型类 ====================
class RemoteSensorData:
    """传感器数据模型"""
    def __init__(self, data: dict):
        self.timestamp = data.get("timestamp", time.time())
        self.device_id = data.get("device_id", "unknown")
        self.temperature = float(data.get("temperature", 0))
        self.humidity = float(data.get("humidity", 0))
        self.aqi = int(data.get("aqi", 0))
        self.tvoc = int(data.get("tvoc", 0))
        self.eco2 = int(data.get("eco2", 0))
        self.object_temp = float(data.get("object_temp", 0))
        self.adult_count = int(data.get("adult_count", 0))
        self.child_count = int(data.get("child_count", 0))
        self.human_detected = bool(data.get("human_detected", False))
        self.child_detected = bool(data.get("child_detected", False))
        self.child_confidence = float(data.get("child_confidence", 0))
        self.door_closed = bool(data.get("door_closed", False))
        self.pir_state = bool(data.get("pir_state", False))
        self.risk_level = data.get("risk_level", "normal")
        
    def to_dict(self):
        """转换为字典格式"""
        return {
            "timestamp": self.timestamp,
            "device_id": self.device_id,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "aqi": self.aqi,
            "tvoc": self.tvoc,
            "eco2": self.eco2,
            "object_temp": self.object_temp,
            "adult_count": self.adult_count,
            "child_count": self.child_count,
            "human_detected": self.human_detected,
            "child_detected": self.child_detected,
            "child_confidence": self.child_confidence,
            "door_closed": self.door_closed,
            "pir_state": self.pir_state,
            "risk_level": self.risk_level
        }

class RemoteDetectionData:
    """检测数据模型"""
    def __init__(self, data: dict):
        self.timestamp = data.get("timestamp", time.time())
        self.device_id = data.get("device_id", "unknown")
        self.child_detected = bool(data.get("child_detected", False))
        self.child_count = int(data.get("child_count", 0))
        self.confidence = float(data.get("confidence", 0))
        self.image_path = data.get("image_path", "")
        
    def to_dict(self):
        """转换为字典格式"""
        return {
            "timestamp": self.timestamp,
            "device_id": self.device_id,
            "child_detected": self.child_detected,
            "child_count": self.child_count,
            "confidence": self.confidence,
            "image_path": self.image_path
        }

# ==================== 数据管理器 ====================
class RemoteDataManager:
    """远程数据管理"""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # 远程传感器数据表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS remote_sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            device_id TEXT,
            temperature REAL,
            humidity REAL,
            aqi INTEGER,
            tvoc INTEGER,
            eco2 INTEGER,
            object_temp REAL,
            human_detected INTEGER,
            child_detected INTEGER,
            child_confidence REAL,
            door_closed INTEGER,
            pir_state INTEGER,
            risk_level TEXT,
            received_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        # 控制命令历史
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS control_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            command TEXT,
            params TEXT,
            result TEXT,
            remote_ip TEXT,
            operator TEXT DEFAULT 'remote'
        )
        ''')
        # 报警记录（从设备端同步）
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS remote_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            level TEXT,
            message TEXT,
            device_id TEXT,
            source TEXT DEFAULT 'device'
        )
        ''')
        # 检测记录表（可选）
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS detection_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            device_id TEXT,
            child_detected INTEGER,
            child_count INTEGER,
            confidence REAL,
            image_path TEXT,
            received_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        # 新增：抓拍图片表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS captured_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            device_id TEXT,
            child_count INTEGER,
            adult_count INTEGER,
            confidence REAL,
            image_data BLOB,
            capture_time TEXT,
            original_width INTEGER,
            original_height INTEGER,
            local_path TEXT,
            received_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        conn.commit()
        conn.close()
    
    def save_sensor_data(self, data: dict):
        """保存传感器数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO remote_sensor_data 
        (timestamp, device_id, temperature, humidity, aqi, tvoc, eco2, 
         object_temp, human_detected, child_detected, child_confidence,
         door_closed, pir_state, risk_level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get("timestamp", time.time()),
            data.get("device_id", "unknown"),
            data.get("temperature", 0),
            data.get("humidity", 0),
            data.get("aqi", 0),
            data.get("tvoc", 0),
            data.get("eco2", 0),
            data.get("object_temp", 0),
            1 if data.get("human_detected", False) else 0,
            1 if data.get("child_detected", False) else 0,
            data.get("child_confidence", 0),
            1 if data.get("door_closed", False) else 0,
            1 if data.get("pir_state", False) else 0,
            data.get("risk_level", "normal")
        ))
        conn.commit()
        conn.close()
    
    def add_sensor_data(self, device_id: str, data: RemoteSensorData):
        """添加传感器数据（优化版）"""
        self.save_sensor_data(data.to_dict())
    
    def add_detection_data(self, data: RemoteDetectionData):
        """添加检测数据（仅记录，不生成报警）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO detection_history 
        (timestamp, device_id, child_detected, child_count, confidence, image_path)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            data.timestamp,
            data.device_id,
            1 if data.child_detected else 0,
            data.child_count,
            data.confidence,
            data.image_path
        ))
        conn.commit()
        conn.close()
    
    def save_synced_alert(self, alert_data: dict):
        """保存从设备端同步来的报警"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO remote_alerts 
        (timestamp, level, message, device_id, source)
        VALUES (?, ?, ?, ?, ?)
        ''', (
            alert_data.get("timestamp", time.time()),
            alert_data.get("level"),
            alert_data.get("message"),
            alert_data.get("device_id", "vehicle_monitor_001"),
            "device"
        ))
        conn.commit()
        conn.close()
    
    def save_control_command(self, command: str, params: dict = None, result: str = "", remote_ip: str = ""):
        """保存控制命令历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO control_history 
        (timestamp, command, params, result, remote_ip)
        VALUES (?, ?, ?, ?, ?)
        ''', (
            time.time(),
            command,
            json.dumps(params) if params else "{}",
            result,
            remote_ip
        ))
        conn.commit()
        conn.close()
    
    def get_latest_data(self, device_id: str = None):
        """获取最新数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        if device_id:
            cursor.execute('''
            SELECT * FROM remote_sensor_data 
            WHERE device_id = ?
            ORDER BY timestamp DESC 
            LIMIT 1
            ''', (device_id,))
        else:
            cursor.execute('''
            SELECT * FROM remote_sensor_data 
            ORDER BY timestamp DESC 
            LIMIT 1
            ''')
        row = cursor.fetchone()
        conn.close()
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None
    
    def get_recent_data(self, hours: int = 24, device_id: str = None):
        """获取最近数据"""
        conn = sqlite3.connect(self.db_path)
        if device_id:
            query = '''
            SELECT * FROM remote_sensor_data 
            WHERE device_id = ? AND received_time >= datetime('now', ?)
            ORDER BY timestamp ASC
            '''
            params = (device_id, f'-{hours} hours')
        else:
            query = '''
            SELECT * FROM remote_sensor_data 
            WHERE received_time >= datetime('now', ?)
            ORDER BY timestamp ASC
            '''
            params = (f'-{hours} hours',)
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        return df
    
    def get_recent_alerts(self, limit: int = 20):
        """获取最近报警记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        SELECT * FROM remote_alerts 
        ORDER BY timestamp DESC 
        LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        conn.close()
        return [dict(zip(columns, row)) for row in rows]
    
    def get_control_history(self, limit: int = 50):
        """获取控制历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        SELECT * FROM control_history 
        ORDER BY timestamp DESC 
        LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        conn.close()
        return [dict(zip(columns, row)) for row in rows]

    def save_captured_image_record(self, data: dict):
        """保存抓拍图片记录到数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO captured_images 
        (timestamp, device_id, child_count, adult_count, confidence, image_data, capture_time, original_width, original_height, local_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get("timestamp", time.time()),
            data.get("device_id", "unknown"),
            data.get("child_count", 0),
            data.get("adult_count", 0),
            data.get("confidence", 0),
            data.get("image_data"),
            data.get("capture_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            data.get("original_width", 0),
            data.get("original_height", 0),
            data.get("local_path", "")
        ))
        conn.commit()
        conn.close()

    def get_captured_images_history(self, limit: int = 50):
        """获取抓拍历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        SELECT * FROM captured_images 
        ORDER BY timestamp DESC 
        LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        conn.close()
        return [dict(zip(columns, row)) for row in rows]

# ==================== 优化的远程通信管理器 ====================
class OptimizedRemoteMQTTManager(QObject):
    """优化版的MQTT管理器"""
    
    # 定义信号
    image_processed_signal = pyqtSignal(dict)  # 图片处理完成信号
    
    def __init__(self, config: RemoteConfig, data_manager: RemoteDataManager):
        super().__init__()
        self.config = config
        self.data_manager = data_manager
        
        # 创建MQTT客户端
        unique_id = str(uuid.uuid4())[:8]
        self.client = mqtt.Client(client_id=f"remote_control_{unique_id}")
        
        # 优化连接参数
        self.client.max_inflight_messages_set(50)
        self.client.max_queued_messages_set(0)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        
        # 设置遗嘱消息
        self.client.will_set("remote/status", json.dumps({
            "device": "remote_control",
            "status": "offline",
            "timestamp": time.time()
        }), qos=1, retain=True)
        
        self.client.keepalive = 60
        
        if config.MQTT_USER and config.MQTT_PASSWORD:
            self.client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
        
        # 设置回调
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        
        # 连接状态
        self.is_connected = False
        self.last_connect_time = 0
        self.reconnect_interval = 5
        
        # 连接状态回调
        self.connection_callback = None
        self.data_callback = None
        self.alert_callback = None
        self.risk_status_callback = None
        
        # 优化队列配置
        self.sensor_queue = queue.Queue(maxsize=500)
        self.detection_queue = queue.Queue(maxsize=200)
        self.alert_queue = queue.Queue(maxsize=100)
        self.image_queue = queue.Queue(maxsize=50)
        
        # 批量处理配置
        self.batch_size = 10
        self.batch_interval = 0.5
        
        # 统计信息
        self.stats = {
            "messages_received": 0,
            "messages_processed": 0,
            "queue_sizes": {
                "sensor": 0,
                "detection": 0,
                "alert": 0,
                "image": 0
            },
            "last_received": time.time(),
            "connection_quality": 0.0
        }
        
        # 消息计数器
        self.message_counter = {
            "sensor": 0,
            "detection": 0,
            "alert": 0,
            "image": 0,
            "dropped": 0
        }
        
        # 性能监控
        self.performance_stats = {
            "avg_process_time": 0,
            "peak_queue_size": 0,
            "throughput_per_min": 0
        }
        
        # 启动性能监控线程
        self.performance_monitor_running = True
        self.performance_monitor_thread = threading.Thread(
            target=self.performance_monitor_worker,
            daemon=True
        )
        self.performance_monitor_thread.start()
    
    def connect(self):
        """连接到MQTT服务器"""
        try:
            print(f"尝试连接MQTT服务器: {self.config.MQTT_BROKER}:{self.config.MQTT_PORT}")
            self.client.connect(self.config.MQTT_BROKER, self.config.MQTT_PORT, 60)
            self.client.loop_start()
            self.last_connect_time = time.time()
            return True
        except Exception as e:
            print(f"MQTT连接失败: {e}")
            return False
    
    def disconnect(self):
        """断开连接"""
        try:
            self.performance_monitor_running = False
            self.client.loop_stop()
            self.client.disconnect()
        except:
            pass
        self.is_connected = False
    
    def on_connect(self, client, userdata, flags, rc):
        """连接成功回调"""
        try:
            if rc == 0:
                self.is_connected = True
                print("远程控制端MQTT连接成功")
                
                for topic_name, topic in self.config.MQTT_TOPICS.items():
                    qos = 1 if topic_name in ["child_detection", "alerts", "captured_image"] else 0
                    client.subscribe(topic, qos=qos)
                    print(f"已订阅主题: {topic} (QoS: {qos})")
                
                self.client.publish(
                    "remote/status",
                    json.dumps({
                        "status": "online",
                        "timestamp": time.time(),
                        "client_id": client._client_id.decode() if hasattr(client._client_id, 'decode') else str(client._client_id)
                    }),
                    qos=1,
                    retain=True
                )
                
                if self.connection_callback:
                    QTimer.singleShot(0, lambda: self.connection_callback(True))
            else:
                print(f"MQTT连接失败，错误码: {rc}")
                self.is_connected = False
                if self.connection_callback:
                    QTimer.singleShot(0, lambda: self.connection_callback(False))
        except Exception as e:
            print(f"连接回调错误: {e}")
            traceback.print_exc()
    
    def on_disconnect(self, client, userdata, rc):
        """MQTT断开连接回调"""
        try:
            self.is_connected = False
            print(f"MQTT连接断开，错误码: {rc}")
            if self.connection_callback:
                QTimer.singleShot(0, lambda: self.connection_callback(False))
            threading.Timer(self.reconnect_interval, self.reconnect).start()
        except Exception as e:
            print(f"断开连接回调错误: {e}")
    
    def reconnect(self):
        """重新连接"""
        if not self.is_connected:
            print("尝试重新连接...")
            self.connect()
    
    def on_message(self, client, userdata, msg):
        """优化MQTT消息回调，减少UI线程压力"""
        try:
            self.stats["messages_received"] += 1
            self.stats["last_received"] = time.time()
            
            topic = msg.topic
            payload = msg.payload.decode()
            
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as e:
                print(f"JSON解析失败: {e}, 原始数据: {payload[:100]}")
                data = {"raw_message": payload}
            
            data["topic"] = topic
            data["received_time"] = time.time()
            data["device_id"] = self.config.DEVICE_ID
            
            if topic == self.config.MQTT_TOPICS["sensor_data"]:
                self.message_counter["sensor"] += 1
                sensor_data = RemoteSensorData(data)
                try:
                    self.sensor_queue.put_nowait(sensor_data)
                except queue.Full:
                    self.message_counter["dropped"] += 1
                    try:
                        self.sensor_queue.get_nowait()
                        self.sensor_queue.put_nowait(sensor_data)
                    except:
                        pass
                        
            elif topic == self.config.MQTT_TOPICS["child_detection"]:
                self.message_counter["detection"] += 1
                detection_data = RemoteDetectionData(data)
                try:
                    self.detection_queue.put_nowait(detection_data)
                except queue.Full:
                    self.message_counter["dropped"] += 1
                    try:
                        self.detection_queue.get_nowait()
                        self.detection_queue.put_nowait(detection_data)
                    except:
                        pass
                        
            elif topic == self.config.MQTT_TOPICS["status"]:
                self.message_counter["sensor"] += 1
                sensor_data = RemoteSensorData(data)
                try:
                    self.sensor_queue.put_nowait(sensor_data)
                except queue.Full:
                    self.message_counter["dropped"] += 1
                    try:
                        self.sensor_queue.get_nowait()
                        self.sensor_queue.put_nowait(sensor_data)
                    except:
                        pass
                        
            elif topic == self.config.MQTT_TOPICS["alerts"]:
                self.message_counter["alert"] += 1
                try:
                    if isinstance(data, dict) and "risk_level" in data:
                        risk_level = data.get("risk_level", "normal")
                        description = data.get("description", "")
                        print(f"远控端收到风险状态同步: {risk_level} - {description}")
                        synced_alert = {
                            "timestamp": data.get("timestamp", time.time()),
                            "level": risk_level,
                            "message": description,
                            "device_id": data.get("device_id", self.config.DEVICE_ID),
                            "source": "device"
                        }
                        try:
                            self.data_manager.save_synced_alert(synced_alert)
                        except Exception as e:
                            print(f"保存同步风险状态失败: {e}")
                        # 触发风险状态和报警更新
                        if self.risk_status_callback:
                            QTimer.singleShot(0, lambda d=data: self.risk_status_callback(d))
                        if self.alert_callback:
                            QTimer.singleShot(0, self.alert_callback)
                    else:
                        print(f"远控端同步收到传统报警: {data.get('level', 'unknown')} - {data.get('message', '')}")
                        try:
                            self.data_manager.save_synced_alert(data)
                        except Exception as e:
                            print(f"保存同步报警失败: {e}")
                        # ===== 关键修改：新增风险状态更新 =====
                        # 无论是否包含risk_level字段，都触发风险状态更新
                        if self.risk_status_callback:
                            QTimer.singleShot(0, lambda d=data: self.risk_status_callback(d))
                        if self.alert_callback:
                            QTimer.singleShot(0, self.alert_callback)
                        try:
                            self.alert_queue.put_nowait(data)
                        except queue.Full:
                            self.message_counter["dropped"] += 1
                            try:
                                self.alert_queue.get_nowait()
                                self.alert_queue.put_nowait(data)
                            except:
                                pass
                except Exception as e:
                    print(f"处理报警消息错误: {e}")
                    traceback.print_exc()
            elif topic == self.config.MQTT_TOPICS["captured_image"]:
                self.message_counter["image"] += 1
                try:
                    self.image_queue.put_nowait(data)
                    print(f"收到抓拍图片: {data.get('detection_type', 'unknown')}")
                except queue.Full:
                    self.message_counter["dropped"] += 1
                    try:
                        self.image_queue.get_nowait()
                        self.image_queue.put_nowait(data)
                    except:
                        pass
                        
            else:
                print(f"收到未知主题消息: {topic}")
            
        except Exception as e:
            print(f"快速消息处理错误: {e}")
            traceback.print_exc()

    def handle_captured_image(self, data: dict):
        """处理接收到的抓拍图片"""
        try:
            print(f"开始处理抓拍图片: {data.get('detection_type', 'unknown')}")
            
            image_base64 = data.get("image_base64")
            if not image_base64:
                print("图片数据为空")
                return
            
            try:
                image_data = base64.b64decode(image_base64)
                print(f"图片解码成功，大小: {len(image_data)} 字节")
            except Exception as e:
                print(f"图片解码失败: {e}")
                return
            
            save_dir = self.config.CAPTURED_IMAGE_DIR
            os.makedirs(save_dir, exist_ok=True)
            timestamp = datetime.fromtimestamp(data.get("timestamp", time.time())).strftime("%Y%m%d_%H%M%S")
            det_type = data.get("detection_type", "unknown")
            filename = f"{save_dir}/capture_{det_type}_{timestamp}.jpg"
            
            try:
                with open(filename, "wb") as f:
                    f.write(image_data)
                print(f"抓拍图片已保存: {filename}")
            except Exception as e:
                print(f"保存图片文件失败: {e}")
                return
            
            data["local_path"] = filename
            data["image_data"] = image_data
            
            try:
                self.data_manager.save_captured_image_record(data)
                print(f"图片记录已保存到数据库")
            except Exception as e:
                print(f"保存图片记录到数据库失败: {e}")
            
            print(f"发出图片处理完成信号")
            self.image_processed_signal.emit(data)
        
        except Exception as e:
            print(f"处理抓拍图片错误: {e}")
            traceback.print_exc()
    
    def get_sensor_data(self, timeout=0.1):
        try:
            return self.sensor_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_detection_data(self, timeout=0.1):
        try:
            return self.detection_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_alert_data(self, timeout=0.1):
        try:
            return self.alert_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_image_data(self, timeout=0.1):
        try:
            return self.image_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def performance_monitor_worker(self):
        """性能监控工作线程（已修复）"""
        last_stats = {
            "messages_received": 0,
            "last_time": time.time()
        }
        
        while self.performance_monitor_running:
            try:
                current_time = time.time()
                
                # 更新队列大小统计
                self.stats["queue_sizes"]["sensor"] = self.sensor_queue.qsize()
                self.stats["queue_sizes"]["detection"] = self.detection_queue.qsize()
                self.stats["queue_sizes"]["alert"] = self.alert_queue.qsize()
                self.stats["queue_sizes"]["image"] = self.image_queue.qsize()
                
                # 计算吞吐量（每分钟消息数）
                elapsed = current_time - last_stats["last_time"]
                if elapsed > 60:  # 每分钟更新一次
                    received_now = self.stats["messages_received"]
                    throughput = (received_now - last_stats["messages_received"]) / (elapsed / 60)
                    self.performance_stats["throughput_per_min"] = throughput
                    
                    last_stats["messages_received"] = received_now
                    last_stats["last_time"] = current_time
                
                # 更新峰值队列大小
                total_queue = sum(self.stats["queue_sizes"].values())
                if total_queue > self.performance_stats["peak_queue_size"]:
                    self.performance_stats["peak_queue_size"] = total_queue
                
                # 简单连接质量评估
                if current_time - self.stats["last_received"] < 30:
                    self.stats["connection_quality"] = 1.0
                elif current_time - self.stats["last_received"] < 120:
                    self.stats["connection_quality"] = 0.5
                else:
                    self.stats["connection_quality"] = 0.0
                
                time.sleep(5)
                
            except Exception as e:
                print(f"性能监控线程异常: {e}")
                time.sleep(5)
    
    def get_stats(self):
        """获取当前统计信息"""
        self.stats["queue_sizes"]["sensor"] = self.sensor_queue.qsize()
        self.stats["queue_sizes"]["detection"] = self.detection_queue.qsize()
        self.stats["queue_sizes"]["alert"] = self.alert_queue.qsize()
        self.stats["queue_sizes"]["image"] = self.image_queue.qsize()
        return {
            "stats": self.stats,
            "message_counter": self.message_counter,
            "performance_stats": self.performance_stats
        }
    
    def evaluate_risk(self, data: dict) -> str:
        return data.get("risk_level", "normal")
    
    def check_alerts(self, data: dict, risk_level: str):
        pass
    
    def send_control_command(self, command: str, params: dict = None, operator: str = "remote"):
        if not self.is_connected:
            return False, "MQTT未连接"
        try:
            message = {
                "command": command,
                "params": params or {},
                "timestamp": time.time(),
                "operator": operator
            }
            result = self.client.publish(
                self.config.MQTT_TOPICS["control"],
                json.dumps(message)
            )
            success = result.rc == mqtt.MQTT_ERR_SUCCESS
            result_msg = "成功" if success else f"失败 (rc={result.rc})"
            self.data_manager.save_control_command(
                command, 
                params or {}, 
                result_msg,
                self.get_client_ip()
            )
            return success, result_msg
        except Exception as e:
            error_msg = f"发送命令失败: {e}"
            self.data_manager.save_control_command(
                command, 
                params or {}, 
                error_msg,
                self.get_client_ip()
            )
            return False, error_msg
    
    def get_client_ip(self):
        try:
            import socket
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except:
            return "unknown"
    
    def register_connection_callback(self, callback):
        self.connection_callback = callback
    
    def register_data_callback(self, callback):
        self.data_callback = callback
    
    def register_alert_callback(self, callback):
        self.alert_callback = callback

# ==================== 远程控制界面（优化版） ====================
class RemoteControlWindow(QMainWindow):
    """远程控制主窗口（优化版）"""
    
    # 定义信号
    update_device_display_signal = pyqtSignal(str, dict)
    update_alerts_signal = pyqtSignal()  # 新增：报警更新信号
    
    def __init__(self, config: RemoteConfig):
        super().__init__()
        self.config = config
        
        # ================= 风险状态控制 =================
        self.device_risk_override = None   # 设备端风险状态（最高优先级）
        
        # 应用样式表
        self.setStyleSheet("""
            QWidget {
                background-color: #f5f5f5;
                font-family: Arial;
                font-size: 13px;
                color: #333;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ddd;
                border-radius: 6px;
                background-color: #ffffff;
                padding: 10px;
                margin-top: 10px;
            }
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
            QPushButton.danger {
                background-color: #f44336;
            }
            QPushButton.danger:hover {
                background-color: #d32f2f;
            }
            QLabel {
                padding: 3px;
            }
            QListWidget {
                background-color: #ffffff;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            QLineEdit, QTextEdit {
                background-color: #ffffff;
                border: 1px solid #ddd;
                border-radius: 4px;
                padding: 5px;
            }
            QTabWidget::pane {
                border: 1px solid #ddd;
                border-radius: 6px;
                background-color: #ffffff;
            }
            QTabBar::tab {
                background-color: #e0e0e0;
                padding: 8px 16px;
                margin-right: 2px;
                border-radius: 4px 4px 0 0;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                border-bottom: 2px solid #4CAF50;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
        """)
        
        # 初始化组件
        self.data_manager = RemoteDataManager(config.DB_PATH)
        self.mqtt_manager = OptimizedRemoteMQTTManager(config, self.data_manager)
        
        # 设置回调 —— 在 mqtt_manager 完全创建后一次性绑定回调
        self.mqtt_manager.risk_status_callback = self.sync_risk_from_alerts
        self.mqtt_manager.alert_callback = self.update_alerts_list
        print("风险状态回调已绑定")
        
        self.current_captured_image = None
        
        # 连接状态
        self.is_connected = False
        self.last_update_time = 0
        
        # 当前设备数据
        self.current_data = None
        self.current_device = config.DEVICE_ID
        
        # 图表数据缓存（用于实时图表）
        self.chart_data_cache = {
            "timestamps": [],
            "temps": [],
            "humids": [],
            "tvocs": [],
            "eco2s": []
        }
        self.max_chart_points = 300  # 显示最近300个数据点
        
        # 数据处理线程标志
        self.data_processor_running = True
        
        # 创建UI
        self.setWindowTitle("车载安全监控系统 - 远程控制端（界面优化版）")
        self.setGeometry(100, 100, 1400, 900)
        
        # 创建中央部件和主布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # 创建状态栏
        self.create_status_bar()
        
        # 创建选项卡
        self.create_tabs()
        
        # 添加到主布局
        main_layout.addWidget(self.tab_widget)
        
        # 启动定时器
        self.start_timers()
        
        # 启动数据处理线程
        self.start_data_processor()
        
        # 连接MQTT
        self.connect_mqtt()
        
        # 连接信号
        self.update_device_display_signal.connect(self.update_device_display_async)
        self.update_alerts_signal.connect(self.update_alerts_list)  # 连接报警更新信号
        self.mqtt_manager.image_processed_signal.connect(self.update_captured_image)  # 连接图片信号
        
        # 新增：强制检查连接状态（延迟执行，确保MQTT有足够时间连接）
        QTimer.singleShot(3000, self.force_check_connection)
        
        # 初始化时立即尝试刷新数据
        QTimer.singleShot(1000, self.refresh_current_data)
        
        # 创建图片保存目录
        os.makedirs(config.CAPTURED_IMAGE_DIR, exist_ok=True)
        
        # 在窗口显示后调整图片标签尺寸
        def adjust_image_label():
            # 确保标签有正确的尺寸
            self.capture_image_label.setFixedSize(480, 360)
            print(f"图片标签尺寸已设置: {self.capture_image_label.size()}")

        QTimer.singleShot(100, adjust_image_label)
    
    def sync_risk_from_alerts(self, risk_data: dict = None):
        """从报警记录同步风险状态，与报警管理共用数据"""
        print(f"sync_risk_from_alerts 被调用")
        
        # 获取最新一条报警记录
        recent_alerts = self.data_manager.get_recent_alerts(1)
        if recent_alerts:
            latest_alert = recent_alerts[0]
            risk_level = latest_alert.get("level", "normal").lower()
            description = latest_alert.get("message", "系统运行正常")
        else:
            risk_level = "normal"
            description = "系统运行正常"
        
        print(f"从报警记录解析出风险等级: {risk_level}, 描述: {description}")
        
        # 同步更新风险卡片
        self.update_risk_indicator(risk_level, description)
        
        print(f"风险状态已同步，当前等级: {risk_level}")
    
    def update_risk_indicator(self, risk_level="normal", description="系统正常，无风险"):
        """更新风险指示器胶囊样式"""
        # 映射风险等级到颜色和文字
        if risk_level == "normal":
            text = "正常"
            color = "#2ecc71"  # 绿色
        elif risk_level == "warning":
            text = "警告"
            color = "#f39c12"  # 橙色
        elif risk_level == "emergency":
            text = "紧急"
            color = "#e74c3c"  # 红色
        else:
            text = "未知"
            color = "#95a5a6"  # 灰色

        # 更新大字胶囊
        self.risk_indicator.setText(text)
        self.risk_indicator.setStyleSheet(f"""
            QLabel {{
                font-size: 32px;
                font-weight: bold;
                color: white;
                background-color: {color};
                padding: 8px 24px;
                border-radius: 20px;
                min-width: 100px;
                max-height: 60px;
            }}
        """)

        # 更新底部说明文字（无背景），先截断过长文本再设置显示
        if description is None:
            description = ""
        if len(description) > 25:
            short_desc = description[:22] + "..."
        else:
            short_desc = description
        self.risk_detail.setText(short_desc)
    
    def on_new_alert(self):
        """新报警到达时的回调"""
        # 触发报警列表更新
        self.update_alerts_list()

    def on_connection_changed(self, connected):
        """MQTT连接状态变化回调"""
        self.is_connected = connected
        if connected:
            print("MQTT连接状态：已连接")
            # 更新UI连接状态
            self.connection_label.setText("已连接")
            self.connection_label.setStyleSheet("color: green; font-weight: bold;")
            self.device_status_label.setText("设备: 在线")
            # 回调已在初始化时绑定，无需在这里重复设置
        else:
            print("MQTT连接状态：断开")
            # 更新UI连接状态
            self.connection_label.setText("连接断开")
            self.connection_label.setStyleSheet("color: red; font-weight: bold;")
            self.device_status_label.setText("设备: 离线")
    
    def force_check_connection(self):
        """强制检查连接状态，如果MQTT未连接则重新连接"""
        if not self.is_connected:
            print("检测到MQTT未连接，尝试重新连接...")
            self.connect_mqtt()
            # 延迟后再次检查
            QTimer.singleShot(2000, self.force_check_connection)
        else:
            print("MQTT连接正常")

    def update_risk_status(self, risk_data: dict):
        """根据设备端同步的风险状态，实时更新仪表盘风险卡片"""
        # ⚠️ 已弃用：风险评估只由设备端驱动
        return
    
    def create_status_bar(self):
        """创建状态栏"""
        self.status_bar = self.statusBar()
        # 连接状态标签
        self.connection_label = QLabel("正在连接...")
        self.connection_label.setStyleSheet("color: orange; font-weight: bold;")
        self.status_bar.addWidget(self.connection_label)
        # 数据时间标签
        self.data_time_label = QLabel("最后更新: 无")
        self.status_bar.addPermanentWidget(self.data_time_label)
        # 设备状态标签
        self.device_status_label = QLabel("设备: 离线")
        self.status_bar.addPermanentWidget(self.device_status_label)
        # 性能状态标签
        self.performance_label = QLabel("性能: --")
        self.status_bar.addPermanentWidget(self.performance_label)
        # 图片接收标签
        self.image_status_label = QLabel("图片: 0")
        self.status_bar.addPermanentWidget(self.image_status_label)
    
    def create_tabs(self):
        """创建选项卡"""
        self.tab_widget = QTabWidget()
        # 仪表盘标签
        dashboard_tab = QWidget()
        self.create_dashboard_tab(dashboard_tab)
        self.tab_widget.addTab(dashboard_tab, "仪表盘")
        # 数据监控标签
        monitor_tab = QWidget()
        self.create_monitor_tab(monitor_tab)
        self.tab_widget.addTab(monitor_tab, "数据监控")
        # 历史记录标签
        history_tab = QWidget()
        self.create_history_tab(history_tab)
        self.tab_widget.addTab(history_tab, "历史记录")
        # 性能监控标签
        performance_tab = QWidget()
        self.create_performance_tab(performance_tab)
        self.tab_widget.addTab(performance_tab, "性能监控")
    
    def create_dashboard_tab(self, parent): 
        """创建仪表盘标签 - 优化布局版本"""
        # 使用滚动区域
        scroll_area = QScrollArea()
        scroll_widget = QWidget()
        main_layout = QVBoxLayout()
        scroll_widget.setLayout(main_layout)
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        
        # 第一行：顶部状态卡片
        top_row_layout = QHBoxLayout()
        
        # 环境监测卡片
        env_group = self.create_environment_card()
        top_row_layout.addWidget(env_group, 1)
        
        # 系统状态卡片
        status_group = self.create_status_card()
        top_row_layout.addWidget(status_group, 1)
        
        # 风险评估卡片
        risk_group = self.create_risk_card()
        top_row_layout.addWidget(risk_group, 1)
        
        main_layout.addLayout(top_row_layout)
        
        # 第二行：人员抓拍和报警管理
        middle_row_layout = QHBoxLayout()
        
        # 人员抓拍卡片（左侧）
        capture_group = self.create_capture_card()
        middle_row_layout.addWidget(capture_group, 2)  # 占2份空间
        
        # 报警管理卡片（右侧）
        alerts_group = self.create_alerts_card()
        middle_row_layout.addWidget(alerts_group, 1)  # 占1份空间
        
        main_layout.addLayout(middle_row_layout)
        
        # 第三行：远程控制和历史记录
        bottom_row_layout = QHBoxLayout()
        
        # 远程控制卡片（左侧）
        control_group = self.create_control_card()
        bottom_row_layout.addWidget(control_group, 2)  # 占2份空间
        
        # 控制历史卡片（右侧）
        history_group = self.create_history_card()
        bottom_row_layout.addWidget(history_group, 1)  # 占1份空间
        
        main_layout.addLayout(bottom_row_layout)
        
        # 设置滚动区域
        parent.setLayout(QVBoxLayout())
        parent.layout().addWidget(scroll_area)
    
    def create_environment_card(self):
        """创建环境监测卡片"""
        group = QGroupBox("🌡️ 环境监测")
        group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)
        
        layout = QGridLayout()
        
        # 温度
        self.temp_label = QLabel("温度: -- °C")
        self.temp_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #e74c3c;")
        layout.addWidget(QLabel("温度:"), 0, 0)
        layout.addWidget(self.temp_label, 0, 1)
        
        # 湿度
        self.humidity_label = QLabel("湿度: -- %")
        self.humidity_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #3498db;")
        layout.addWidget(QLabel("湿度:"), 1, 0)
        layout.addWidget(self.humidity_label, 1, 1)
        
        # CO2
        self.co2_label = QLabel("CO₂: -- ppm")
        self.co2_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #2ecc71;")
        layout.addWidget(QLabel("CO₂:"), 2, 0)
        layout.addWidget(self.co2_label, 2, 1)
        
        # TVOC
        self.tvoc_label = QLabel("TVOC: -- ppb")
        self.tvoc_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #9b59b6;")
        layout.addWidget(QLabel("TVOC:"), 3, 0)
        layout.addWidget(self.tvoc_label, 3, 1)
        
        # AQI
        self.aqi_label = QLabel("AQI: --")
        self.aqi_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #f39c12;")
        layout.addWidget(QLabel("AQI:"), 4, 0)
        layout.addWidget(self.aqi_label, 4, 1)
        
        group.setLayout(layout)
        return group
    
    def create_status_card(self):
        """创建系统状态卡片"""
        group = QGroupBox("🚗 系统状态")
        group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)
        
        layout = QGridLayout()
        
        # 车门状态
        self.door_label = QLabel("车门: --")
        self.door_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(QLabel("车门:"), 0, 0)
        layout.addWidget(self.door_label, 0, 1)
        
        # PIR状态
        self.pir_label = QLabel("运动: --")
        self.pir_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(QLabel("运动:"), 1, 0)
        layout.addWidget(self.pir_label, 1, 1)
        
        # 人体温度
        self.object_temp_label = QLabel("人体温度: -- °C")
        self.object_temp_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #e67e22;")
        layout.addWidget(QLabel("人体温度:"), 2, 0)
        layout.addWidget(self.object_temp_label, 2, 1)
        
        # 成人检测
        self.human_label = QLabel("成人检测: --")
        self.human_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(QLabel("成人检测:"), 3, 0)
        layout.addWidget(self.human_label, 3, 1)
        
        # 儿童检测
        self.child_label = QLabel("儿童检测: --")
        self.child_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(QLabel("儿童检测:"), 4, 0)
        layout.addWidget(self.child_label, 4, 1)
        
        group.setLayout(layout)
        return group
    
    def create_risk_card(self):
        """创建风险评估卡片"""
        group = QGroupBox("⚠️ 风险评估")
        group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)  # 减小间距

        # 风险指示器 - 紧凑彩色胶囊
        self.risk_indicator = QLabel("正常")
        self.risk_indicator.setAlignment(Qt.AlignCenter)
        self.risk_indicator.setWordWrap(False)
        self.risk_indicator.setStyleSheet("""
            QLabel {
                font-size: 32px;  /* 稍微减小字体 */
                font-weight: bold;
                color: white;
                background-color: #2ecc71;
                padding: 8px 24px;  /* 减小内边距 */
                border-radius: 20px;
                min-width: 100px;
                max-height: 60px;  /* 限制最大高度 */
            }
        """)
        # 关键：让 QLabel 尺寸自适应内容（胶囊紧贴文字）
        self.risk_indicator.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # 风险详情 - 简洁文字说明，单行显示
        self.risk_detail = QLabel("系统正常，无风险")
        self.risk_detail.setAlignment(Qt.AlignCenter)
        self.risk_detail.setWordWrap(False)  # 禁止换行，单行显示
        self.risk_detail.setStyleSheet("""
            font-size: 14px;  /* 减小字体 */
            color: #333;
            background: transparent;
            padding: 5px 15px;  /* 减小内边距 */
            margin-top: 5px;
            max-height: 25px;  /* 限制最大高度 */
            qproperty-alignment: 'AlignCenter';
        """)

        # 统计信息 - 改为两行两列，更紧凑的布局
        stats_widget = QWidget()
        stats_layout = QGridLayout()
        stats_layout.setSpacing(5)  # 减小网格间距
        stats_layout.setContentsMargins(10, 5, 10, 5)  # 设置内边距

        # 第一行：温度和CO2
        self.risk_stats_temp = QLabel("温度: --")
        self.risk_stats_temp.setStyleSheet("""
            font-size: 12px;
            color: #666;
            padding: 2px;
            qproperty-alignment: 'AlignLeft';
        """)

        self.risk_stats_co2 = QLabel("CO₂: --")
        self.risk_stats_co2.setStyleSheet("""
            font-size: 12px;
            color: #666;
            padding: 2px;
            qproperty-alignment: 'AlignLeft';
        """)

        # 第二行：TVOC和车门
        self.risk_stats_tvoc = QLabel("TVOC: --")
        self.risk_stats_tvoc.setStyleSheet("""
            font-size: 12px;
            color: #666;
            padding: 2px;
            qproperty-alignment: 'AlignLeft';
        """)

        self.risk_stats_door = QLabel("车门: --")
        self.risk_stats_door.setStyleSheet("""
            font-size: 12px;
            color: #666;
            padding: 2px;
            qproperty-alignment: 'AlignLeft';
        """)

        # 添加到网格布局
        stats_layout.addWidget(self.risk_stats_temp, 0, 0)  # 第一行第一列
        stats_layout.addWidget(self.risk_stats_co2, 0, 1)   # 第一行第二列
        stats_layout.addWidget(self.risk_stats_tvoc, 1, 0)  # 第二行第一列
        stats_layout.addWidget(self.risk_stats_door, 1, 1)  # 第二行第二列

        # 设置列宽策略，确保平均分布
        stats_layout.setColumnStretch(0, 1)
        stats_layout.setColumnStretch(1, 1)

        stats_widget.setLayout(stats_layout)

        # 添加到主布局
        layout.addWidget(self.risk_indicator, alignment=Qt.AlignCenter)
        layout.addWidget(self.risk_detail, alignment=Qt.AlignCenter)
        layout.addWidget(stats_widget)

        group.setLayout(layout)
        self.risk_card = group
        return group
    
    def create_capture_card(self):
        """创建人员抓拍卡片"""
        group = QGroupBox("📸 最新人员抓拍")
        group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)
        
        layout = QVBoxLayout()
        
        # 图片显示区域
        image_container = QWidget()
        image_layout = QHBoxLayout()
        
        self.capture_image_label = QLabel()
        self.capture_image_label.setAlignment(Qt.AlignCenter)
        self.capture_image_label.setMinimumSize(480, 360)
        self.capture_image_label.setMaximumSize(480, 360)
        self.capture_image_label.setStyleSheet("""
            border: 2px solid #ddd;
            background-color: #f8f9fa;
            border-radius: 8px;
            padding: 10px;
        """)
        self.capture_image_label.setText("等待人员检测抓拍...")
        
        # 信息面板
        info_widget = QWidget()
        info_layout = QVBoxLayout()
        
        # 时间信息
        self.capture_time_label = QLabel("抓拍时间: --")
        self.capture_time_label.setStyleSheet("font-size: 14px; color: #666;")
        
        # 检测信息
        self.capture_info_label = QLabel("检测类型: 无检测")
        self.capture_info_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #333;")
        
        # 人员统计
        self.capture_stats_label = QLabel("检测结果: 等待中...")
        self.capture_stats_label.setStyleSheet("font-size: 13px; color: #666;")
        
        # 置信度
        self.capture_confidence_label = QLabel("置信度: --")
        self.capture_confidence_label.setStyleSheet("font-size: 13px; color: #666;")
        
        info_layout.addWidget(self.capture_time_label)
        info_layout.addWidget(self.capture_info_label)
        info_layout.addWidget(self.capture_stats_label)
        info_layout.addWidget(self.capture_confidence_label)
        info_layout.addStretch()
        
        info_widget.setLayout(info_layout)
        
        image_layout.addWidget(self.capture_image_label)
        image_layout.addWidget(info_widget)
        image_container.setLayout(image_layout)
        
        layout.addWidget(image_container)
        
        # 按钮区域
        button_widget = QWidget()
        button_layout = QHBoxLayout()
        
        self.save_image_btn = QPushButton("💾 保存图片")
        self.save_image_btn.clicked.connect(self.save_captured_image)
        self.save_image_btn.setEnabled(False)
        self.save_image_btn.setStyleSheet("padding: 8px 16px;")
        
        self.view_all_btn = QPushButton("📚 查看历史")
        self.view_all_btn.clicked.connect(self.show_capture_history)
        self.view_all_btn.setStyleSheet("padding: 8px 16px;")
        
        self.refresh_image_btn = QPushButton("🔄 刷新")
        self.refresh_image_btn.clicked.connect(self.refresh_captured_image)
        self.refresh_image_btn.setStyleSheet("padding: 8px 16px;")
        
        button_layout.addWidget(self.save_image_btn)
        button_layout.addWidget(self.view_all_btn)
        button_layout.addWidget(self.refresh_image_btn)
        button_layout.addStretch()
        
        button_widget.setLayout(button_layout)
        layout.addWidget(button_widget)
        
        group.setLayout(layout)
        return group
    
    def refresh_captured_image(self):
        """刷新抓拍图片显示"""
        try:
            # 从数据库获取最新抓拍图片
            images = self.data_manager.get_captured_images_history(1)
            if images and len(images) > 0:
                latest_image = images[0]
                
                # 模拟数据格式
                data = {
                    "image_data": latest_image.get("image_data"),
                    "capture_time": latest_image.get("capture_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    "timestamp": latest_image.get("timestamp", time.time()),
                    "child_count": latest_image.get("child_count", 0),
                    "adult_count": latest_image.get("adult_count", 0),
                    "confidence": latest_image.get("confidence", 0),
                    "device_id": latest_image.get("device_id", "unknown"),
                    "detection_type": "历史记录" if latest_image.get("child_count", 0) > 0 else "无人员"
                }
                
                self.update_captured_image(data)
                print("抓拍图片已刷新")
            else:
                self.capture_image_label.setText("暂无抓拍图片")
                self.capture_info_label.setText("检测类型: 无检测")
                self.capture_stats_label.setText("检测结果: 无数据")
                self.capture_confidence_label.setText("置信度: --")
                self.capture_time_label.setText("抓拍时间: --")
        except Exception as e:
            print(f"刷新抓拍图片错误: {e}")
    
    def create_alerts_card(self):
        """创建报警管理卡片"""
        group = QGroupBox("🚨 报警管理")
        group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)
        
        layout = QVBoxLayout()
        
        # 报警统计
        stats_widget = QWidget()
        stats_layout = QGridLayout()
        
        self.total_alerts_label = QLabel("总报警数: 0")
        self.total_alerts_label.setStyleSheet("font-size: 14px;")
        
        self.emergency_alerts_label = QLabel("紧急报警: 0")
        self.emergency_alerts_label.setStyleSheet("font-size: 14px; color: #e74c3c;")
        
        self.warning_alerts_label = QLabel("警告报警: 0")
        self.warning_alerts_label.setStyleSheet("font-size: 14px; color: #f39c12;")
        
        self.today_alerts_label = QLabel("今日报警: 0")
        self.today_alerts_label.setStyleSheet("font-size: 14px;")
        
        self.last_alert_time_label = QLabel("最近报警: 无")
        self.last_alert_time_label.setStyleSheet("font-size: 12px; color: #666;")
        
        stats_layout.addWidget(self.total_alerts_label, 0, 0)
        stats_layout.addWidget(self.emergency_alerts_label, 0, 1)
        stats_layout.addWidget(self.warning_alerts_label, 1, 0)
        stats_layout.addWidget(self.today_alerts_label, 1, 1)
        stats_layout.addWidget(self.last_alert_time_label, 2, 0, 1, 2)
        
        stats_widget.setLayout(stats_layout)
        layout.addWidget(stats_widget)
        
        # 报警列表
        self.alerts_list = QListWidget()
        self.alerts_list.setMaximumHeight(200)
        self.alerts_list.setSelectionMode(QListWidget.NoSelection)  # 禁止选择
        layout.addWidget(self.alerts_list)
        
        group.setLayout(layout)
        return group
    
    def create_control_card(self):
        """创建远程控制卡片"""
        group = QGroupBox("🎮 远程控制")
        group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)
        
        layout = QVBoxLayout()
        
        # 控制按钮网格
        grid_widget = QWidget()
        grid_layout = QGridLayout()
        
        # 第一行按钮
        self.lower_window_btn = QPushButton("⬇️ 一键降窗")
        self.lower_window_btn.clicked.connect(self.lower_windows)
        self.lower_window_btn.setToolTip("发送降窗命令，降低车窗通风")
        self.lower_window_btn.setStyleSheet("padding: 10px;")
        
        self.test_alarm_btn = QPushButton("🔊 测试报警")
        self.test_alarm_btn.clicked.connect(self.test_alarm)
        self.test_alarm_btn.setToolTip("测试声光报警系统")
        self.test_alarm_btn.setStyleSheet("padding: 10px;")
        
        # 第二行按钮
        self.send_sms_btn = QPushButton("📱 发送短信")
        self.send_sms_btn.clicked.connect(self.send_sms)
        self.send_sms_btn.setToolTip("发送测试短信到预设手机号")
        self.send_sms_btn.setStyleSheet("padding: 10px;")
        
        self.reset_btn = QPushButton("🔄 系统复位")
        self.reset_btn.clicked.connect(self.reset_system)
        self.reset_btn.setToolTip("重置系统状态，停止所有报警并重置冷却时间")
        self.reset_btn.setStyleSheet("padding: 10px; background-color: #f39c12;")
        
        # 第三行按钮
        self.close_window_btn = QPushButton("⬆️ 一键关窗")
        self.close_window_btn.clicked.connect(self.close_windows)
        self.close_window_btn.setToolTip("关闭车窗")
        self.close_window_btn.setStyleSheet("padding: 10px;")
        
        self.view_history_btn = QPushButton("📜 查看控制历史")
        self.view_history_btn.clicked.connect(self.show_control_history)
        self.view_history_btn.setToolTip("查看控制命令历史")
        self.view_history_btn.setStyleSheet("padding: 10px;")
        
        # 布局按钮
        grid_layout.addWidget(self.lower_window_btn, 0, 0)
        grid_layout.addWidget(self.test_alarm_btn, 0, 1)
        grid_layout.addWidget(self.send_sms_btn, 1, 0)
        grid_layout.addWidget(self.reset_btn, 1, 1)
        grid_layout.addWidget(self.close_window_btn, 2, 0)
        grid_layout.addWidget(self.view_history_btn, 2, 1)
        
        grid_widget.setLayout(grid_layout)
        layout.addWidget(grid_widget)
        
        # 连接状态提示
        status_widget = QWidget()
        status_layout = QHBoxLayout()
        
        self.control_status_label = QLabel("控制状态: 等待连接...")
        self.control_status_label.setStyleSheet("font-size: 12px; color: #666;")
        
        status_layout.addWidget(self.control_status_label)
        status_layout.addStretch()
        
        status_widget.setLayout(status_layout)
        layout.addWidget(status_widget)
        
        group.setLayout(layout)
        return group
    
    def create_history_card(self):
        """创建控制历史卡片"""
        group = QGroupBox("📋 最近控制历史")
        group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)
        
        layout = QVBoxLayout()
        
        self.control_history_list = QListWidget()
        self.control_history_list.setMaximumHeight(180)
        layout.addWidget(self.control_history_list)
        
        group.setLayout(layout)
        return group
    
    def create_monitor_tab(self, parent):
        """创建数据监控标签 - 包含实时数据表和图表"""
        layout = QVBoxLayout()
        parent.setLayout(layout)
        
        # 创建选项卡用于切换不同视图
        monitor_tabs = QTabWidget()
        
        # 实时数据选项卡
        realtime_tab = QWidget()
        realtime_layout = QVBoxLayout()
        
        # 实时数据表
        table_group = QGroupBox("实时数据")
        table_layout = QVBoxLayout()
        # 创建表格
        self.data_table = QTableWidget()
        self.data_table.setColumnCount(10)
        self.data_table.setHorizontalHeaderLabels([
            "时间", "温度", "湿度", "CO₂", "TVOC", "AQI", 
            "人体温度", "车门", "运动", "风险等级"
        ])
        self.data_table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self.data_table)
        table_group.setLayout(table_layout)
        realtime_layout.addWidget(table_group)
        
        realtime_tab.setLayout(realtime_layout)
        monitor_tabs.addTab(realtime_tab, "实时数据")
        
        # 温湿度图表选项卡
        temp_humidity_tab = QWidget()
        temp_humidity_layout = QVBoxLayout()
        
        # 温湿度实时图表
        temp_humidity_group = QGroupBox("温湿度实时趋势")
        temp_humidity_chart_layout = QVBoxLayout()
        
        self.plot_temp_hum = pg.PlotWidget(title="温湿度实时趋势")
        self.plot_temp_hum.setLabel('left', '温度 (°C) / 湿度 (%)')
        self.plot_temp_hum.setLabel('bottom', '时间')
        self.plot_temp_hum.showGrid(x=True, y=True, alpha=0.3)
        self.plot_temp_hum.setYRange(0, 100)
        self.plot_temp_hum.addLegend()
        
        # 创建温度和湿度曲线
        self.curve_temp = self.plot_temp_hum.plot([], [], pen=pg.mkPen('r', width=3), name="温度")
        self.curve_hum = self.plot_temp_hum.plot([], [], pen=pg.mkPen('b', width=3), name="湿度")
        
        temp_humidity_chart_layout.addWidget(self.plot_temp_hum)
        temp_humidity_group.setLayout(temp_humidity_chart_layout)
        temp_humidity_layout.addWidget(temp_humidity_group)
        
        temp_humidity_tab.setLayout(temp_humidity_layout)
        monitor_tabs.addTab(temp_humidity_tab, "温湿度图表")
        
        # 空气质量图表选项卡
        air_quality_tab = QWidget()
        air_quality_layout = QVBoxLayout()
        
        # 空气质量实时图表
        air_quality_group = QGroupBox("空气质量实时趋势")
        air_quality_chart_layout = QVBoxLayout()
        
        self.plot_air = pg.PlotWidget(title="TVOC & eCO2 实时趋势")
        self.plot_air.setLabel('left', '浓度')
        self.plot_air.setLabel('bottom', '时间')
        self.plot_air.showGrid(x=True, y=True, alpha=0.3)
        self.plot_air.addLegend()
        
        self.curve_tvoc = self.plot_air.plot([], [], pen=pg.mkPen('g', width=3), name="TVOC (ppb)")
        self.curve_eco2 = self.plot_air.plot([], [], pen=pg.mkPen('m', width=3), name="eCO2 (ppm)")
        
        air_quality_chart_layout.addWidget(self.plot_air)
        air_quality_group.setLayout(air_quality_chart_layout)
        air_quality_layout.addWidget(air_quality_group)
        
        air_quality_tab.setLayout(air_quality_layout)
        monitor_tabs.addTab(air_quality_tab, "空气质量图表")
        
        layout.addWidget(monitor_tabs)
    
    def create_history_tab(self, parent):
        """创建历史记录标签"""
        layout = QVBoxLayout()
        parent.setLayout(layout)
        
        # 数据导出组
        export_group = QGroupBox("数据导出")
        export_layout = QGridLayout()
        
        # 时间范围选择
        export_layout.addWidget(QLabel("时间范围:"), 0, 0)
        self.time_range_combo = QComboBox()
        self.time_range_combo.addItems(["最近1小时", "最近24小时", "最近7天", "最近30天", "自定义"])
        export_layout.addWidget(self.time_range_combo, 0, 1)
        
        # 自定义时间选择
        self.custom_date_edit = QDateEdit()
        self.custom_date_edit.setCalendarPopup(True)
        self.custom_date_edit.setDate(QDate.currentDate())
        self.custom_date_edit.setEnabled(False)
        export_layout.addWidget(self.custom_date_edit, 0, 2)
        
        # 数据格式
        export_layout.addWidget(QLabel("导出格式:"), 1, 0)
        self.export_format_combo = QComboBox()
        self.export_format_combo.addItems(["CSV", "JSON", "Excel"])
        export_layout.addWidget(self.export_format_combo, 1, 1)
        
        # 导出按钮
        self.export_btn = QPushButton("导出数据")
        self.export_btn.clicked.connect(self.export_data)
        export_layout.addWidget(self.export_btn, 1, 2)
        
        export_group.setLayout(export_layout)
        layout.addWidget(export_group)
        
        # 历史数据查看
        history_group = QGroupBox("历史数据查看")
        history_layout = QVBoxLayout()
        # 创建历史数据表格
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(12)
        self.history_table.setHorizontalHeaderLabels([
            "时间", "设备ID", "温度", "湿度", "CO₂", "TVOC", "AQI",
            "人体温度", "车门", "运动", "儿童检测", "风险等级"
        ])
        history_layout.addWidget(self.history_table)
        history_group.setLayout(history_layout)
        layout.addWidget(history_group)
        
        # 连接时间范围选择变化信号
        self.time_range_combo.currentTextChanged.connect(self.on_time_range_changed)
    
    def create_performance_tab(self, parent):
        """创建性能监控标签"""
        layout = QVBoxLayout()
        parent.setLayout(layout)
        
        # 性能指标组
        metrics_group = QGroupBox("性能指标")
        metrics_layout = QGridLayout()
        
        # 消息统计
        metrics_layout.addWidget(QLabel("接收消息数:"), 0, 0)
        self.messages_received_label = QLabel("0")
        metrics_layout.addWidget(self.messages_received_label, 0, 1)
        
        metrics_layout.addWidget(QLabel("处理消息数:"), 1, 0)
        self.messages_processed_label = QLabel("0")
        metrics_layout.addWidget(self.messages_processed_label, 1, 1)
        
        metrics_layout.addWidget(QLabel("丢弃消息数:"), 2, 0)
        self.messages_dropped_label = QLabel("0")
        metrics_layout.addWidget(self.messages_dropped_label, 2, 1)
        
        # 队列状态
        metrics_layout.addWidget(QLabel("传感器队列:"), 3, 0)
        self.sensor_queue_label = QLabel("0")
        metrics_layout.addWidget(self.sensor_queue_label, 3, 1)
        
        metrics_layout.addWidget(QLabel("检测队列:"), 4, 0)
        self.detection_queue_label = QLabel("0")
        metrics_layout.addWidget(self.detection_queue_label, 4, 1)
        
        metrics_layout.addWidget(QLabel("报警队列:"), 5, 0)
        self.alert_queue_label = QLabel("0")
        metrics_layout.addWidget(self.alert_queue_label, 5, 1)
        
        metrics_layout.addWidget(QLabel("图片队列:"), 6, 0)
        self.image_queue_label = QLabel("0")
        metrics_layout.addWidget(self.image_queue_label, 6, 1)
        
        metrics_layout.addWidget(QLabel("峰值队列:"), 7, 0)
        self.peak_queue_label = QLabel("0")
        metrics_layout.addWidget(self.peak_queue_label, 7, 1)
        
        # 吞吐量
        metrics_layout.addWidget(QLabel("吞吐量(消息/分):"), 8, 0)
        self.throughput_label = QLabel("0")
        metrics_layout.addWidget(self.throughput_label, 8, 1)
        
        # 连接质量
        metrics_layout.addWidget(QLabel("连接质量:"), 9, 0)
        self.connection_quality_label = QLabel("0%")
        metrics_layout.addWidget(self.connection_quality_label, 9, 1)
        
        # 图片接收统计
        metrics_layout.addWidget(QLabel("图片接收数:"), 10, 0)
        self.image_received_label = QLabel("0")
        metrics_layout.addWidget(self.image_received_label, 10, 1)
        
        metrics_group.setLayout(metrics_layout)
        layout.addWidget(metrics_group)
        
        # 实时性能图表
        chart_group = QGroupBox("性能趋势")
        chart_layout = QVBoxLayout()
        self.performance_chart = pg.PlotWidget(title="队列大小趋势")
        self.performance_chart.setLabel('left', '队列大小')
        self.performance_chart.setLabel('bottom', '时间')
        self.performance_chart.showGrid(x=True, y=True, alpha=0.3)
        self.queue_curve = self.performance_chart.plot([], [], pen='b', name="队列大小")
        chart_layout.addWidget(self.performance_chart)
        chart_group.setLayout(chart_layout)
        layout.addWidget(chart_group)
        
        # 性能数据缓存
        self.performance_data_cache = []
        self.performance_time_cache = []
        
        layout.addStretch()
    
    def start_data_processor(self):
        """启动数据处理线程"""
        self.data_processor_thread = threading.Thread(
            target=self.data_processor_worker,
            daemon=True
        )
        self.data_processor_thread.start()
    
    def data_processor_worker(self):
        """独立的数据处理工作线程"""
        while self.data_processor_running:
            try:
                # 批量处理传感器数据
                sensor_batch = []
                for _ in range(self.mqtt_manager.batch_size):
                    try:
                        data = self.mqtt_manager.get_sensor_data(0.1)
                        if data:
                            sensor_batch.append(data)
                    except queue.Empty:
                        break
                if sensor_batch:
                    self.batch_process_sensor_data(sensor_batch)
                # 批量处理检测数据
                detection_batch = []
                for _ in range(self.mqtt_manager.batch_size):
                    try:
                        data = self.mqtt_manager.get_detection_data(0.1)
                        if data:
                            detection_batch.append(data)
                    except queue.Empty:
                        break
                if detection_batch:
                    self.batch_process_detection_data(detection_batch)
                # 处理报警数据
                alert_batch = []
                for _ in range(self.mqtt_manager.batch_size):
                    try:
                        data = self.mqtt_manager.get_alert_data(0.1)
                        if data:
                            alert_batch.append(data)
                    except queue.Empty:
                        break
                if alert_batch:
                    self.batch_process_alert_data(alert_batch)
                # 处理图片数据 - 这是关键，确保图片被处理
                image_batch = []
                for _ in range(self.mqtt_manager.batch_size):
                    try:
                        data = self.mqtt_manager.get_image_data(0.1)
                        if data:
                            image_batch.append(data)
                    except queue.Empty:
                        break
                if image_batch:
                    self.batch_process_image_data(image_batch)
                time.sleep(self.mqtt_manager.batch_interval)
            except Exception as e:
                print(f"数据处理线程错误: {e}")
                traceback.print_exc()
            time.sleep(1)
    
    def batch_process_sensor_data(self, batch):
        """批量处理传感器数据"""
        for data in batch:
            # 评估风险等级（仅评估，不生成报警）
            risk_level = self.mqtt_manager.evaluate_risk(data.to_dict())
            data.risk_level = risk_level
            
            # 保存到数据库
            self.data_manager.add_sensor_data(data.device_id, data)
            
            # 不再检查报警，报警由设备端同步
            
            # 更新统计信息
            self.mqtt_manager.stats["messages_processed"] += 1
            
            # 使用信号槽机制异步更新UI
            self.update_device_display_signal.emit(data.device_id, data.to_dict())
    
    def batch_process_detection_data(self, batch):
        """批量处理检测数据"""
        for data in batch:
            # 保存检测数据（不生成报警）
            self.data_manager.add_detection_data(data)
            
            # 更新统计信息
            self.mqtt_manager.stats["messages_processed"] += 1
    
    def batch_process_alert_data(self, batch):
        """批量处理报警数据"""
        for data in batch:
            # 报警已在前面的on_message中保存到数据库
            # 这里只需更新UI
            self.update_alerts_signal.emit()
            
            # 更新统计信息
            self.mqtt_manager.stats["messages_processed"] += 1
    
    def batch_process_image_data(self, batch):
        """批量处理图片数据"""
        for data in batch:
            try:
                print(f"开始处理图片数据: {data.get('detection_type', 'unknown')}")
                
                # 检查数据是否包含必要字段
                if 'image_base64' not in data:
                    print("警告：图片数据缺少image_base64字段")
                    continue
                    
                # 直接调用MQTT管理器的处理函数
                self.mqtt_manager.handle_captured_image(data)
                
                # 更新统计信息
                self.mqtt_manager.stats["messages_processed"] += 1
                
                print(f"图片处理完成")
            except Exception as e:
                print(f"处理图片数据错误: {e}")
                traceback.print_exc()
    
    def start_timers(self):
        """启动定时器（优化版）"""
        # 更新UI显示 - 降低到2秒一次，与设备端发送频率匹配
        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self.update_ui_display)
        self.ui_timer.start(2000)  # 从500毫秒改为2秒
        
        # 数据刷新定时器
        self.data_refresh_timer = QTimer()
        self.data_refresh_timer.timeout.connect(self.refresh_current_data)
        self.data_refresh_timer.start(5000)  # 每5秒从数据库刷新一次
        
        # 连接状态检查定时器
        self.connection_timer = QTimer()
        self.connection_timer.timeout.connect(self.check_and_update_connection)
        self.connection_timer.start(2000)  # 每2秒检查一次连接状态
        
        # 性能监控定时器
        self.performance_timer = QTimer()
        self.performance_timer.timeout.connect(self.update_performance_metrics)
        self.performance_timer.start(3000)  # 每3秒更新一次性能指标
        
        # 实时数据表更新定时器
        self.table_timer = QTimer()
        self.table_timer.timeout.connect(self.update_data_table)
        self.table_timer.start(3000)  # 每3秒更新一次数据表
    
    def check_and_update_connection(self):
        """检查和更新连接状态"""
        try:
            # 如果MQTT管理器已连接但UI显示未连接
            if hasattr(self.mqtt_manager, 'is_connected'):
                # 更新本地连接状态
                self.is_connected = self.mqtt_manager.is_connected
                
                if self.mqtt_manager.is_connected:
                    if self.connection_label.text() != "已连接":
                        self.connection_label.setText("已连接")
                        self.connection_label.setStyleSheet("color: green; font-weight: bold;")
                        self.device_status_label.setText("设备: 在线")
                        print("连接状态已更新为：已连接")
                        
                        # 更新控制状态标签
                        self.control_status_label.setText("控制状态: 已连接，可发送命令")
                        self.control_status_label.setStyleSheet("font-size: 12px; color: #2ecc71;")
                        
                        # 立即更新按钮状态
                        self.update_ui_display()
                else:
                    if self.connection_label.text() != "连接断开":
                        self.connection_label.setText("连接断开")
                        self.connection_label.setStyleSheet("color: red; font-weight: bold;")
                        self.device_status_label.setText("设备: 离线")
                        print("连接状态已更新为：断开")
                        
                        # 更新控制状态标签
                        self.control_status_label.setText("控制状态: 连接断开，无法发送命令")
                        self.control_status_label.setStyleSheet("font-size: 12px; color: #e74c3c;")
                        
                        # 尝试重新连接
                        QTimer.singleShot(5000, self.connect_mqtt)
        except Exception as e:
            print(f"检查和更新连接状态错误: {e}")
            traceback.print_exc()
    
    def connect_mqtt(self):
        """连接MQTT服务器"""
        success = self.mqtt_manager.connect()
        if not success:
            QTimer.singleShot(5000, self.connect_mqtt)  # 5秒后重试
    
    def update_device_display_async(self, device_id: str, data: dict):
        """异步更新设备显示（通过信号槽）"""
        # ===== 禁止本地刷新覆盖设备风险 =====
        if self.device_risk_override is not None:
            pass  # 允许更新其他UI，但不处理风险评估
            
        try:
            if device_id == self.current_device:
                self.current_data = data
                self.last_update_time = time.time()
                
                # 更新UI显示（但不包括风险卡片）
                self.update_ui_with_data(data)
                
                # 更新数据时间显示
                timestamp = data.get("timestamp", time.time())
                if isinstance(timestamp, (int, float)):
                    update_time = datetime.fromtimestamp(timestamp)
                    self.data_time_label.setText(f"最后更新: {update_time.strftime('%H:%M:%S')}")
                
                # 更新设备状态
                self.device_status_label.setText("设备: 在线")
        except Exception as e:
            print(f"异步更新设备显示错误: {e}")
            traceback.print_exc()
    
    def update_temp_humidity_chart(self):
        """单独更新温湿度图表"""
        try:
            if len(self.chart_data_cache["timestamps"]) < 2:
                return
            times = self.chart_data_cache["timestamps"]
            temps = self.chart_data_cache["temps"]
            humids = self.chart_data_cache["humids"]
            
            t0 = times[0]
            rel_times = [(t - t0) / 60 for t in times]  # 相对时间（分钟）
            
            self.curve_temp.setData(rel_times, temps)
            self.curve_hum.setData(rel_times, humids)
        except Exception as e:
            print(f"更新温湿度图表错误: {e}")

    def update_air_quality_chart(self):
        """单独更新空气质量图表"""
        try:
            if len(self.chart_data_cache["timestamps"]) < 2:
                return
            times = self.chart_data_cache["timestamps"]
            tvocs = self.chart_data_cache["tvocs"]
            eco2s = self.chart_data_cache["eco2s"]
            
            t0 = times[0]
            rel_times = [(t - t0) / 60 for t in times]
            
            self.curve_tvoc.setData(rel_times, tvocs)
            self.curve_eco2.setData(rel_times, eco2s)
        except Exception as e:
            print(f"更新空气质量图表错误: {e}")

    def update_ui_with_data(self, data: dict):
        """使用给定数据更新UI"""
        try:
            # 更新环境监测
            temp = float(data.get('temperature', 0))
            self.temp_label.setText(f"{temp:.1f} °C")
            humidity = float(data.get('humidity', 0))
            self.humidity_label.setText(f"{humidity:.1f} %")
            eco2 = int(data.get('eco2', 0))
            self.co2_label.setText(f"{eco2} ppm")
            tvoc = int(data.get('tvoc', 0))
            self.tvoc_label.setText(f"{tvoc} ppb")
            # AQI评级
            aqi_value = int(data.get('aqi', 0))
            aqi_rating = {1: "优", 2: "良", 3: "中", 4: "差", 5: "极差"}
            aqi_text = f"{aqi_value} ({aqi_rating.get(aqi_value, '未知')})"
            self.aqi_label.setText(aqi_text)
            
            # 更新系统状态
            door_closed = bool(data.get('door_closed', False))
            self.door_label.setText(f"{'关闭' if door_closed else '打开'}")
            
            pir_state = bool(data.get('pir_state', False))
            self.pir_label.setText(f"{'是' if pir_state else '否'}")
            
            object_temp = float(data.get('object_temp', 0))
            self.object_temp_label.setText(f"{object_temp:.1f} °C")
            
            adult_count = int(data.get('adult_count', 0))
            child_count = int(data.get('child_count', 0))
            
            adult_detected = (adult_count > 0 and child_count == 0)
            child_detected = (child_count > 0)
            
            self.human_label.setText(f"{'是' if adult_detected else '否'}")
            self.child_label.setText(f"{'是' if child_detected else '否'}")
            
            # 更新风险统计信息（如果数据中有）
            if hasattr(self, 'risk_stats_temp'):
                temp_val = float(data.get('temperature', 0))
                self.risk_stats_temp.setText(f"温度: {temp_val:.1f}°C")

            if hasattr(self, 'risk_stats_co2'):
                eco2_val = int(data.get('eco2', 0))
                self.risk_stats_co2.setText(f"CO₂: {eco2_val}ppm")

            if hasattr(self, 'risk_stats_tvoc'):
                tvoc_val = int(data.get('tvoc', 0))
                self.risk_stats_tvoc.setText(f"TVOC: {tvoc_val}ppb")

            if hasattr(self, 'risk_stats_door'):
                door_closed_val = bool(data.get('door_closed', False))
                self.risk_stats_door.setText(f"车门: {'关' if door_closed_val else '开'}")
            
            # 更新图表缓存
            timestamp = data.get('timestamp', time.time())
            
            # 限制缓存大小
            if len(self.chart_data_cache['timestamps']) >= self.max_chart_points:
                for key in self.chart_data_cache:
                    if self.chart_data_cache[key]:
                        self.chart_data_cache[key].pop(0)
            
            self.chart_data_cache['timestamps'].append(timestamp)
            self.chart_data_cache['temps'].append(temp)
            self.chart_data_cache['humids'].append(humidity)
            self.chart_data_cache['tvocs'].append(tvoc)
            self.chart_data_cache['eco2s'].append(eco2)
            
            # 更新图表
            try:
                self.update_temp_humidity_chart()
                self.update_air_quality_chart()
            except Exception:
                pass
            
        except Exception as e:
            print(f"更新UI数据错误: {e}")
            traceback.print_exc()
    
    def refresh_current_data(self):
        """从数据库获取最新数据并更新显示"""
        try:
            # 从数据库获取最新数据
            latest_data = self.data_manager.get_latest_data()
            if latest_data:
                # 处理数据格式
                processed_data = {}
                # 转换字段类型
                for key, value in latest_data.items():
                    if value is None:
                        processed_data[key] = 0 if key in ["temperature", "humidity", "object_temp", "child_confidence"] else ""
                    else:
                        processed_data[key] = value
                # 确保布尔字段正确
                bool_fields = ["human_detected", "child_detected", "door_closed", "pir_state"]
                for field in bool_fields:
                    if field in processed_data:
                        if isinstance(processed_data[field], (int, float)):
                            processed_data[field] = bool(processed_data[field])
                        elif isinstance(processed_data[field], str):
                            processed_data[field] = processed_data[field].lower() == "true"
                self.current_data = processed_data
                self.last_update_time = time.time()
                # 更新UI（不包括风险卡片）
                self.update_ui_with_data(processed_data)
                # 更新数据时间显示
                if "timestamp" in processed_data:
                    timestamp = processed_data["timestamp"]
                    if isinstance(timestamp, (int, float)):
                        update_time = datetime.fromtimestamp(timestamp)
                        self.data_time_label.setText(f"最后更新: {update_time.strftime('%H:%M:%S')}")
                # 更新设备状态
                self.device_status_label.setText("设备: 在线")
                return True
            else:
                return False
        except Exception as e:
            print(f"刷新数据错误: {e}")
            traceback.print_exc()
        return False
    
    def update_ui_display(self):
        """更新UI显示（优化版）"""
        try:
            # 更新控制按钮状态
            connected = self.is_connected
            self.lower_window_btn.setEnabled(connected)
            self.test_alarm_btn.setEnabled(connected)
            self.send_sms_btn.setEnabled(connected)
            self.reset_btn.setEnabled(connected)
            self.close_window_btn.setEnabled(connected)
            self.view_history_btn.setEnabled(True)  # 查看历史按钮始终可用
            
            # 更新报警列表
            self.update_alerts_list()
            
            # 更新控制历史
            self.update_control_history()
            
        except Exception as e:
            print(f"更新UI显示错误: {e}")
            traceback.print_exc()
    
    def update_data_table(self):
        """更新实时数据表格"""
        try:
            # 获取最近10条数据
            conn = sqlite3.connect(self.config.DB_PATH)
            query = '''
            SELECT * FROM remote_sensor_data 
            ORDER BY timestamp DESC 
            LIMIT 10
            '''
            df = pd.read_sql_query(query, conn)
            conn.close()
            
            if df.empty:
                return
                
            # 设置表格行数
            self.data_table.setRowCount(len(df))
            
            # 填充数据
            for i, row in df.iterrows():
                # 时间
                timestamp = row.get('timestamp', 0)
                if isinstance(timestamp, (int, float)):
                    time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
                else:
                    time_str = str(timestamp)
                self.data_table.setItem(i, 0, QTableWidgetItem(time_str))
                
                # 温度
                temp = row.get('temperature', 0)
                self.data_table.setItem(i, 1, QTableWidgetItem(f"{temp:.1f}"))
                
                # 湿度
                humidity = row.get('humidity', 0)
                self.data_table.setItem(i, 2, QTableWidgetItem(f"{humidity:.1f}"))
                
                # CO2
                eco2 = row.get('eco2', 0)
                self.data_table.setItem(i, 3, QTableWidgetItem(str(eco2)))
                
                # TVOC
                tvoc = row.get('tvoc', 0)
                self.data_table.setItem(i, 4, QTableWidgetItem(str(tvoc)))
                
                # AQI
                aqi = row.get('aqi', 0)
                aqi_rating = {1: "优", 2: "良", 3: "中", 4: "差", 5: "极差"}
                aqi_text = f"{aqi} ({aqi_rating.get(aqi, '未知')})"
                self.data_table.setItem(i, 5, QTableWidgetItem(aqi_text))
                
                # 人体温度
                object_temp = row.get('object_temp', 0)
                self.data_table.setItem(i, 6, QTableWidgetItem(f"{object_temp:.1f}"))
                
                # 车门状态
                door_closed = bool(row.get('door_closed', False))
                self.data_table.setItem(i, 7, QTableWidgetItem("关闭" if door_closed else "打开"))
                
                # 运动状态
                pir_state = bool(row.get('pir_state', False))
                self.data_table.setItem(i, 8, QTableWidgetItem("是" if pir_state else "否"))
                
                # 风险等级
                risk_level = row.get('risk_level', 'normal')
                risk_item = QTableWidgetItem(risk_level)
                if risk_level == 'emergency':
                    risk_item.setForeground(QColor("red"))
                    risk_item.setBackground(QColor(255, 230, 230))
                elif risk_level == 'warning':
                    risk_item.setForeground(QColor("orange"))
                    risk_item.setBackground(QColor(255, 245, 230))
                else:
                    risk_item.setForeground(QColor("green"))
                    risk_item.setBackground(QColor(230, 255, 230))
                self.data_table.setItem(i, 9, risk_item)
                
        except Exception as e:
            print(f"更新数据表格错误: {e}")
            traceback.print_exc()
    
    def update_performance_metrics(self):
        """更新性能指标"""
        try:
            stats = self.mqtt_manager.get_stats()
            
            # 更新消息统计
            self.messages_received_label.setText(str(stats["stats"]["messages_received"]))
            self.messages_processed_label.setText(str(stats["stats"]["messages_processed"]))
            self.messages_dropped_label.setText(str(stats["message_counter"]["dropped"]))
            
            # 更新队列状态
            self.sensor_queue_label.setText(str(stats["stats"]["queue_sizes"]["sensor"]))
            self.detection_queue_label.setText(str(stats["stats"]["queue_sizes"]["detection"]))
            self.alert_queue_label.setText(str(stats["stats"]["queue_sizes"]["alert"]))
            self.image_queue_label.setText(str(stats["stats"]["queue_sizes"]["image"]))
            self.peak_queue_label.setText(str(stats["performance_stats"]["peak_queue_size"]))
            
            # 更新吞吐量
            throughput = stats["performance_stats"]["throughput_per_min"]
            self.throughput_label.setText(f"{throughput:.1f}")
            
            # 更新连接质量
            quality = stats["stats"]["connection_quality"] * 100
            self.connection_quality_label.setText(f"{quality:.1f}%")
            self.performance_label.setText(f"质量: {quality:.0f}%")
            
            # 更新图片接收统计
            self.image_received_label.setText(str(stats["message_counter"]["image"]))
            self.image_status_label.setText(f"图片: {stats['message_counter']['image']}")
            
            # 更新性能图表
            current_time = time.time()
            total_queue = (stats["stats"]["queue_sizes"]["sensor"] + 
                          stats["stats"]["queue_sizes"]["detection"] + 
                          stats["stats"]["queue_sizes"]["alert"] +
                          stats["stats"]["queue_sizes"]["image"])
            
            # 限制缓存大小
            if len(self.performance_time_cache) > 60:  # 最多保存60个点
                self.performance_time_cache.pop(0)
                self.performance_data_cache.pop(0)
            
            self.performance_time_cache.append(current_time)
            self.performance_data_cache.append(total_queue)
            
            # 更新图表
            if len(self.performance_time_cache) > 1:
                times = [(t - self.performance_time_cache[0]) for t in self.performance_time_cache]
                self.performance_chart.clear()
                self.performance_chart.plot(times, self.performance_data_cache, pen='b', name="队列大小")
            
        except Exception as e:
            print(f"更新性能指标错误: {e}")
            traceback.print_exc()
    
    def update_alerts_list(self):
        """更新报警列表（同步风险状态）"""
        try:
            # 获取最近报警记录
            alerts = self.data_manager.get_recent_alerts(15)
            
            # === 防重复刷新逻辑 ===
            if len(self.alerts_list) == len(alerts) and len(alerts) > 0:
                last_item = self.alerts_list.item(0)
                if last_item:
                    last_item_text = last_item.text()
                    
                    latest_alert = alerts[-1]
                    time_str = datetime.fromtimestamp(latest_alert.get('timestamp', time.time())).strftime("%Y-%m-%d %H:%M:%S")
                    level = (latest_alert.get('level') or 'info').upper()
                    message = (latest_alert.get('message') or '')[:100]
                    new_text = f"[{time_str}] {level}: {message}"
                    expected_text = new_text
                    
                    if last_item_text == expected_text:
                        self.update_alert_stats()
                        return
            
            # === 如果有变化，才清空并重新填充 ===
            self.alerts_list.clear()
            
            for alert in reversed(alerts):
                time_str = datetime.fromtimestamp(alert.get('timestamp', time.time())).strftime("%Y-%m-%d %H:%M:%S")
                level = alert.get('level', 'info')
                message = (alert.get('message') or '')[:100]
                
                item_text = f"[{time_str}] {level.upper()}: {message}"
                item = QListWidgetItem(item_text)
                
                # 颜色设置
                if level == 'emergency':
                    item.setForeground(QColor("red"))
                    item.setBackground(QColor(255, 230, 230))
                elif level == 'warning':
                    item.setForeground(QColor("orange"))
                    item.setBackground(QColor(255, 245, 230))
                elif level == 'info':
                    item.setForeground(QColor("blue"))
                    item.setBackground(QColor(230, 240, 255))
                else:
                    item.setForeground(QColor("gray"))
                    item.setBackground(QColor(245, 245, 245))
                
                self.alerts_list.addItem(item)
            
            # 更新统计
            self.update_alert_stats()
            
            # 新增：同步风险状态
            self.sync_risk_from_alerts()
            
        except Exception as e:
            print(f"更新报警列表错误: {e}")
            traceback.print_exc()
    
    def update_alert_stats(self):
        """更新报警统计（简化版）"""
        try:
            conn = sqlite3.connect(self.config.DB_PATH)
            cursor = conn.cursor()
            
            # 总报警数
            cursor.execute("SELECT COUNT(*) FROM remote_alerts")
            total = cursor.fetchone()[0]
            
            # 紧急报警数
            cursor.execute("SELECT COUNT(*) FROM remote_alerts WHERE level = 'emergency'")
            emergency = cursor.fetchone()[0]
            
            # 警告报警数
            cursor.execute("SELECT COUNT(*) FROM remote_alerts WHERE level = 'warning'")
            warning = cursor.fetchone()[0]
            
            # 今日报警数
            today = datetime.now().strftime("%Y-%m-%d")
            cursor.execute(
                "SELECT COUNT(*) FROM remote_alerts WHERE date(datetime(timestamp, 'unixepoch')) = ?",
                (today,)
            )
            today_alerts = cursor.fetchone()[0]
            
            # 最近报警时间
            cursor.execute("SELECT MAX(timestamp) FROM remote_alerts")
            last_alert_time = cursor.fetchone()[0]
            
            conn.close()
            
            self.total_alerts_label.setText(f"总报警数: {total}")
            self.emergency_alerts_label.setText(f"紧急报警: {emergency}")
            self.warning_alerts_label.setText(f"警告报警: {warning}")
            self.today_alerts_label.setText(f"今日报警: {today_alerts}")
            
            if last_alert_time:
                last_alert_str = datetime.fromtimestamp(last_alert_time).strftime("%H:%M:%S")
                self.last_alert_time_label.setText(f"最近报警: {last_alert_str}")
            else:
                self.last_alert_time_label.setText("最近报警: 无")
                
        except Exception as e:
            print(f"更新报警统计错误: {e}")
            traceback.print_exc()
    
    def update_control_history(self):
        """更新控制历史"""
        try:
            history = self.data_manager.get_control_history(10)  # 只显示最近10条
            self.control_history_list.clear()
            for record in history:
                time_str = datetime.fromtimestamp(record['timestamp']).strftime("%H:%M:%S")
                command = record['command']
                params = json.loads(record['params']) if record['params'] else {}
                result = record['result']
                # 构建显示文本
                param_text = ""
                if params:
                    param_text = f" ({json.dumps(params)})"
                item_text = f"[{time_str}] {command}{param_text} - {result}"
                item = QListWidgetItem(item_text)
                # 根据结果设置颜色
                if "成功" in result:
                    item.setForeground(QColor("green"))
                else:
                    item.setForeground(QColor("red"))
                self.control_history_list.addItem(item)
        except Exception as e:
            print(f"更新控制历史错误: {e}")
            traceback.print_exc()
    
    def show_control_history(self):
        """显示完整的控制历史"""
        try:
            history = self.data_manager.get_control_history(50)  # 获取50条历史记录
            
            # 创建对话框显示完整历史
            dialog = QDialog(self)
            dialog.setWindowTitle("控制命令历史")
            dialog.setGeometry(400, 300, 800, 500)
            
            layout = QVBoxLayout()
            dialog.setLayout(layout)
            
            # 创建文本编辑框显示历史
            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setFont(QFont("Courier", 10))
            
            # 格式化历史信息
            history_text = "控制命令历史:\n\n"
            for record in history:
                time_str = datetime.fromtimestamp(record['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
                command = record['command']
                params = json.loads(record['params']) if record['params'] else {}
                result = record['result']
                remote_ip = record.get('remote_ip', '未知')
                
                history_text += f"时间: {time_str}\n"
                history_text += f"命令: {command}\n"
                if params:
                    history_text += f"参数: {json.dumps(params, ensure_ascii=False)}\n"
                history_text += f"结果: {result}\n"
                history_text += f"来源IP: {remote_ip}\n"
                history_text += "-" * 60 + "\n"
            
            text_edit.setText(history_text)
            layout.addWidget(text_edit)
            
            # 添加关闭按钮
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(dialog.close)
            layout.addWidget(close_btn)
            
            dialog.exec_()
            
        except Exception as e:
            print(f"显示控制历史错误: {e}")
            traceback.print_exc()
    
    def on_time_range_changed(self, text):
        """时间范围选择变化"""
        self.custom_date_edit.setEnabled(text == "自定义")
    
    def lower_windows(self):
        """一键降窗"""
        reply = QMessageBox.question(
            self, "确认降窗",
            "确定要降窗 100% 吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            success, message = self.mqtt_manager.send_control_command(
                "lower_window", 
                {"percent": 100},
                "远程控制"
            )
            if success:
                QMessageBox.information(self, "成功", "降窗命令已发送")
            else:
                QMessageBox.warning(self, "失败", f"发送降窗命令失败: {message}")
    
    def test_alarm(self):
        """测试报警"""
        reply = QMessageBox.question(
            self, "确认测试",
            "确定要测试报警系统吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            success, message = self.mqtt_manager.send_control_command(
                "test_alarm",
                {},
                "远程控制"
            )
            if success:
                QMessageBox.information(self, "成功", "测试报警命令已发送")
            else:
                QMessageBox.warning(self, "失败", f"发送测试报警命令失败: {message}")
    
    def send_sms(self):
        """发送短信"""
        reply = QMessageBox.question(
            self, "确认发送短信",
            "确定要发送测试短信吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            success, message = self.mqtt_manager.send_control_command(
                "send_sms",
                {},
                "远程控制"
            )
            if success:
                QMessageBox.information(self, "成功", "短信命令已发送")
            else:
                QMessageBox.warning(self, "失败", f"发送短信命令失败: {message}")
    
    def force_stop_local_alarm_state(self):
        """强制将远控端UI恢复到'正常'状态，与设备端复位后一致"""
        # 风险指示器恢复绿色正常状态
        self.update_risk_indicator("normal", "系统已复位，所有报警状态已停止")

        print("远控端本地报警状态已强制停止")
    
    def reset_system(self):
        """系统复位"""
        reply = QMessageBox.warning(
            self,
            "确认系统复位",
            "确定要复位系统吗？这将：\n\n"
            "• 向设备端发送复位命令\n"
            "• 停止本地所有报警显示\n"
            "• 将风险指示器恢复为正常（绿色）\n"
            "• 重置冷却时间（设备端执行）\n"
            "• 清除模拟数据（设备端执行）\n\n"
            "此操作不可撤销！",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.No:
            return

        # 1. 发送复位命令到设备端
        success, message = self.mqtt_manager.send_control_command(
            "reset_system",
            {},
            "远程控制"
        )

        # 2. 无论命令是否发送成功，都立即在远控端本地"停止报警"
        self.force_stop_local_alarm_state()

        # 3. 给出提示
        if success:
            QMessageBox.information(
                self,
                "复位成功",
                "系统复位命令已发送至设备端\n"
                "本地报警显示已停止，界面恢复正常状态"
            )
        else:
            QMessageBox.warning(
                self,
                "部分成功",
                "本地报警显示已停止，但复位命令发送失败：\n" + message
            )
            
        # 4. 同步更新风险指示器（可选增强）
        self.sync_risk_from_alerts({
            "risk_level": "normal",
            "description": "系统已复位，所有报警状态已停止",
            "is_simulated": False
        })
    
    def close_windows(self):
        """关窗"""
        reply = QMessageBox.question(
            self, "确认关窗",
            "确定要关闭车窗吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            success, message = self.mqtt_manager.send_control_command(
                "close_window",
                {},
                "远程控制"
            )
            if success:
                QMessageBox.information(self, "成功", "关窗命令已发送")
            else:
                QMessageBox.warning(self, "失败", f"发送关窗命令失败: {message}")
    
    def export_data(self):
        """导出数据"""
        try:
            # 获取时间范围
            time_range = self.time_range_combo.currentText()
            export_format = self.export_format_combo.currentText()
            # 计算时间范围
            now = datetime.now()
            if time_range == "最近1小时":
                start_time = now - timedelta(hours=1)
            elif time_range == "最近24小时":
                start_time = now - timedelta(days=1)
            elif time_range == "最近7天":
                start_time = now - timedelta(days=7)
            elif time_range == "最近30天":
                start_time = now - timedelta(days=30)
            elif time_range == "自定义":
                start_date = self.custom_date_edit.date().toPyDate()
                start_time = datetime(start_date.year, start_date.month, start_date.day)
            else:
                start_time = now - timedelta(hours=1)
            # 获取数据
            conn = sqlite3.connect(self.config.DB_PATH)
            query = '''
            SELECT * FROM remote_sensor_data 
            WHERE received_time >= ?
            ORDER BY timestamp DESC
            '''
            df = pd.read_sql_query(query, conn, params=(start_time,))
            conn.close()
            if df.empty:
                QMessageBox.warning(self, "无数据", "指定时间范围内没有数据")
                return
            # 选择保存文件
            formats = {
                "CSV": "CSV文件 (*.csv)",
                "JSON": "JSON文件 (*.json)",
                "Excel": "Excel文件 (*.xlsx)"
            }
            options = QFileDialog.Options()
            filename, selected_filter = QFileDialog.getSaveFileName(
                self, "导出数据", "",
                f"{formats[export_format]};;所有文件 (*)", 
                options=options
            )
            if not filename:
                return
            # 根据格式导出
            if export_format == "CSV":
                if not filename.endswith('.csv'):
                    filename += '.csv'
                df.to_csv(filename, index=False, encoding='utf-8')
            elif export_format == "JSON":
                if not filename.endswith('.json'):
                    filename += '.json'
                df.to_json(filename, orient='records', force_ascii=False, indent=2)
            elif export_format == "Excel":
                if not filename.endswith('.xlsx'):
                    filename += '.xlsx'
                df.to_excel(filename, index=False)
            QMessageBox.information(self, "导出成功", f"数据已导出到: {filename}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出数据时出错: {str(e)}")
    
    def update_captured_image(self, data):
        """更新抓拍图片显示"""
        print(f"update_captured_image 被调用，数据keys: {list(data.keys())}")
        
        try:
            # 优先使用二进制图片数据
            image_data = data.get("image_data")
            
            if image_data:
                print(f"使用二进制图片数据，大小: {len(image_data)} 字节")
            else:
                # 如果没有二进制数据，尝试从base64解码
                image_base64 = data.get("image_base64")
                if image_base64:
                    print(f"从base64解码图片，base64长度: {len(image_base64)}")
                    image_data = base64.b64decode(image_base64)
                    data["image_data"] = image_data  # 保存解码后的数据
                else:
                    print("没有图片数据")
                    return
           
            # 创建QImage
            image = QImage()
            if not image.loadFromData(image_data, "jpg"):  # 假设是jpg格式
                # 尝试其他格式
                if not image.loadFromData(image_data):
                    print("图片加载失败")
                    return
           
            print(f"图片加载成功，尺寸: {image.width()}x{image.height()}")
           
            # 保存当前图片数据
            self.current_captured_image = {
                "image": image,
                "data": image_data,
                "timestamp": data.get("timestamp", time.time()),
                "child_count": data.get("child_count", 0),
                "adult_count": data.get("adult_count", 0),
                "confidence": data.get("confidence", 0),
                "device_id": data.get("device_id", "unknown"),
                "capture_time": data.get("capture_time", time.strftime("%Y-%m-%d %H:%M:%S")),
                "width": data.get("original_width", image.width()),
                "height": data.get("original_height", image.height()),
                "local_path": data.get("local_path", ""),
                "detection_type": data.get("detection_type", "unknown")
            }
           
            # 更新UI
            self.display_captured_image(image, data)
           
        except Exception as e:
            print(f"更新图片显示错误: {e}")
            traceback.print_exc()
   
    def display_captured_image(self, image, data):
        """在UI上显示图片"""
        print("display_captured_image 被调用")
        
        if image.isNull():
            print("错误：传入的QImage为空")
            return
            
        try:
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                print("错误：从QImage创建QPixmap失败")
                return
                
            # 计算合适的显示大小
            label_size = self.capture_image_label.size()
            if label_size.width() <= 0 or label_size.height() <= 0:
                # 使用最小尺寸作为后备
                label_size = QSize(480, 360)
                
            print(f"标签尺寸: {label_size.width()}x{label_size.height()}")
            print(f"原始图片尺寸: {image.width()}x{image.height()}")
            
            # 缩放以适应标签，保持宽高比
            scaled_pixmap = pixmap.scaled(
                label_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            
            print(f"缩放后尺寸: {scaled_pixmap.width()}x{scaled_pixmap.height()}")
            
            # 设置图片
            self.capture_image_label.setPixmap(scaled_pixmap)
            self.capture_image_label.setText("")  # 清除等待文本
            
            # 更新信息标签
            time_str = data.get("capture_time", datetime.fromtimestamp(data["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"))
            detection_type = data.get("detection_type", "未知")
            
            self.capture_time_label.setText(f"抓拍时间: {time_str}")
            self.capture_info_label.setText(f"检测类型: {detection_type}")
           
            # 更新人员信息
            child_count = data.get("child_count", 0)
            adult_count = data.get("adult_count", 0)
            confidence = data.get("confidence", 0)
           
            stats_text = f"检测结果: {child_count}名儿童, {adult_count}名成人"
            self.capture_stats_label.setText(stats_text)
           
            self.capture_confidence_label.setText(f"置信度: {confidence:.2f}")
           
            # 根据是否有儿童设置不同的颜色
            if child_count > 0:
                self.capture_stats_label.setStyleSheet("font-size: 13px; color: red; font-weight: bold;")
            else:
                self.capture_stats_label.setStyleSheet("font-size: 13px; color: #666;")
           
            # 启用保存按钮
            self.save_image_btn.setEnabled(True)
            
        except Exception as e:
            print(f"显示图片错误: {e}")
            traceback.print_exc()
   
    def save_captured_image(self):
        """保存当前显示的图片"""
        if not self.current_captured_image:
            return
       
        # 图片已经保存到本地，提示用户保存位置
        local_path = self.current_captured_image.get("local_path", "")
        if local_path and os.path.exists(local_path):
            # 打开文件所在目录
            try:
                import subprocess
                if sys.platform == "win32":
                    os.startfile(os.path.dirname(local_path))
                elif sys.platform == "darwin":  # macOS
                    subprocess.call(["open", os.path.dirname(local_path)])
                else:  # Linux
                    subprocess.call(["xdg-open", os.path.dirname(local_path)])
                QMessageBox.information(self, "图片已保存", f"图片已保存至:\n{local_path}")
            except Exception as e:
                QMessageBox.information(self, "图片已保存", f"图片已保存至:\n{local_path}")
        else:
            # 如果本地路径不存在，让用户选择保存位置
            options = QFileDialog.Options()
            file_name, _ = QFileDialog.getSaveFileName(
                self,
                "保存抓拍图片",
                f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg",
                "JPEG图像 (*.jpg);;PNG图像 (*.png);;所有文件 (*)",
                options=options
            )
           
            if file_name:
                try:
                    # 保存图片
                    with open(file_name, 'wb') as f:
                        f.write(self.current_captured_image["data"])
                    QMessageBox.information(self, "保存成功", f"图片已保存至:\n{file_name}")
                except Exception as e:
                    QMessageBox.warning(self, "保存失败", f"无法保存图片: {str(e)}")

    def show_capture_history(self):
        """显示抓拍历史"""
        try:
            history = self.data_manager.get_captured_images_history(50)
            
            # 创建对话框
            dialog = QDialog(self)
            dialog.setWindowTitle("抓拍历史")
            dialog.setGeometry(400, 300, 800, 500)
            
            layout = QVBoxLayout()
            dialog.setLayout(layout)
            
            # 创建列表
            list_widget = QListWidget()
            for record in history:
                time_str = record['capture_time']
                child_count = record['child_count']
                adult_count = record['adult_count']
                confidence = record['confidence']
                item_text = f"[{time_str}] 儿童: {child_count}, 成人: {adult_count}, 置信度: {confidence:.2f}"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, record['id'])
                list_widget.addItem(item)
            
            layout.addWidget(list_widget)
            
            # 添加查看按钮
            view_btn = QPushButton("查看选中图片")
            view_btn.clicked.connect(lambda: self.view_selected_capture(list_widget))
            layout.addWidget(view_btn)
            
            # 添加关闭按钮
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(dialog.close)
            layout.addWidget(close_btn)
            
            dialog.exec_()
            
        except Exception as e:
            print(f"显示抓拍历史错误: {e}")
            traceback.print_exc()
    
    def view_selected_capture(self, list_widget):
        """查看选中的抓拍图片"""
        selected_items = list_widget.selectedItems()
        if not selected_items:
            return
        item = selected_items[0]
        capture_id = item.data(Qt.UserRole)
        
        # 从数据库获取
        conn = sqlite3.connect(self.config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM captured_images WHERE id = ?", (capture_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            columns = ['id', 'timestamp', 'device_id', 'child_count', 'adult_count', 'confidence', 'image_data', 'capture_time', 'original_width', 'original_height', 'local_path']
            data = dict(zip(columns, row))
            image_data = data['image_data']
            
            # 显示在新窗口
            view_dialog = QDialog(self)
            view_dialog.setWindowTitle("查看抓拍")
            view_layout = QVBoxLayout()
            
            # 创建标签显示图片
            label = QLabel()
            image = QImage()
            image.loadFromData(image_data)
            pixmap = QPixmap.fromImage(image)
            
            # 缩放图片以适应窗口
            scaled_pixmap = pixmap.scaled(600, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(scaled_pixmap)
            label.setAlignment(Qt.AlignCenter)
            
            view_layout.addWidget(label)
            
            # 添加图片信息
            info_label = QLabel(f"时间: {data['capture_time']}\n"
                               f"儿童: {data['child_count']}, 成人: {data['adult_count']}\n"
                               f"置信度: {data['confidence']:.2f}")
            view_layout.addWidget(info_label)
            
            # 添加保存按钮
            save_btn = QPushButton("保存图片")
            save_btn.clicked.connect(lambda: self.save_specific_image(image_data, data))
            view_layout.addWidget(save_btn)
            
            # 添加关闭按钮
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(view_dialog.close)
            view_layout.addWidget(close_btn)
            
            view_dialog.setLayout(view_layout)
            view_dialog.resize(650, 550)
            view_dialog.exec_()
    
    def save_specific_image(self, image_data, data):
        """保存特定图片"""
        options = QFileDialog.Options()
        time_str = data['capture_time'].replace(':', '-').replace(' ', '_')
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "保存图片",
            f"capture_{time_str}.jpg",
            "JPEG图像 (*.jpg);;PNG图像 (*.png);;所有文件 (*)",
            options=options
        )
        
        if file_name:
            try:
                with open(file_name, 'wb') as f:
                    f.write(image_data)
                QMessageBox.information(self, "保存成功", f"图片已保存至:\n{file_name}")
            except Exception as e:
                QMessageBox.warning(self, "保存失败", f"无法保存图片: {str(e)}")
    
    def closeEvent(self, event):
        """窗口关闭事件"""
        try:
            self.data_processor_running = False
            self.mqtt_manager.disconnect()
        except:
            pass
        event.accept()

# ==================== 主程序入口 ====================
if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        # 直接创建主窗口，无需登录
        config = RemoteConfig()
        window = RemoteControlWindow(config)
        window.show()
        sys.exit(app.exec_())
    except Exception as e:
        print(f"主程序异常: {e}")
        traceback.print_exc()
        input("按Enter键退出...")