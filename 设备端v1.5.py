# v1.5 main.py - Python设备端主程序（整合v0.4和v1.0功能，包含界面美化和短信报警）更新自动降窗逻辑+模拟测试+MQTT发送优化+报警同步发布+图片抓拍功能
"""
车载儿童安全监控系统 - Python上位机端
整合通信管理、数据处理、视觉识别、GUI界面、报警通知、短信报警和模拟测试功能
作者：方钦炯
日期：2025年12月1日
新增：增强系统复位功能，完全停止所有运行状态并重置冷却时间
新增：MQTT发送优化，添加频率限制和独立发送线程
新增：报警同步发布功能，将报警信息同步发布到远控端
新增：图片抓拍功能，检测到人员时自动抓拍并发送
新增：自动抓拍开关功能，可在界面上开启/关闭抓拍功能
新增：配置持久化功能，阈值等设置保存到JSON文件，无需重启即可生效
"""

import sys
import os
import json
import time
import threading
import asyncio
import sqlite3
import pandas as pd
import numpy as np
import uuid
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional, Dict, List, Any, Tuple
import queue
from pyqtgraph import PlotWidget, mkPen
from scipy.interpolate import make_interp_spline

# ==================== 第三方库导入 ====================
try:
    import paho.mqtt.client as mqtt
    import serial
    import cv2
    import torch
    from ultralytics import YOLO
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    import seaborn as sns
    from PyQt5.QtWidgets import *
    from PyQt5.QtCore import *
    from PyQt5.QtGui import *
    from PyQt5.Qt3DCore import *
    from PyQt5.Qt3DExtras import *
    from PyQt5.Qt3DRender import *
    import pyqtgraph as pg
    import requests
    import logging
    from logging.handlers import RotatingFileHandler
    import base64
    import traceback
    print("所有依赖库导入成功")
except ImportError as e:
    print(f"缺少依赖库: {e}")
    print("请安装: pip install paho-mqtt pyserial opencv-python torch ultralytics pandas numpy matplotlib seaborn pyqt5 pyqtgraph requests")
    sys.exit(1)

# 修复matplotlib中文显示问题
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

# ==================== 配置类（支持持久化）====================
class Config:
    """系统配置 - 支持持久化"""
    
    CONFIG_FILE = "system_config.json"
    
    # 默认值（首次运行或配置文件损坏时使用）
    DEFAULTS = {
        #"MQTT_BROKER": "509pk6184bc5.vicp.fun",http://22.tcp.cpolar.top:12007/ sj.frp.one
        "MQTT_BROKER": "broker.emqx.io",
        "MQTT_PORT": 1883,
        "MQTT_USER": "",
        "MQTT_PASSWORD": "",
        "THRESHOLDS": {
            "temperature_high": 35.0,
            "temperature_extreme": 40.0,
            "humidity_high": 70.0,
            "co2_high": 1000,
            "co2_extreme": 1500,
            "tvoc_high": 500,
            "tvoc_extreme": 1000
        },
        "AUTO_CAPTURE_ENABLED": True
    }
    
    def __init__(self):
        self.load_config()
    
    def load_config(self):
        """从文件加载配置，没有则使用默认值并保存"""
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # 加载到实例属性
                for key, value in data.items():
                    if key == "THRESHOLDS":
                        self.THRESHOLDS = value
                    else:
                        setattr(self, key, value)
                print("配置加载成功")
            except Exception as e:
                print(f"加载配置失败，使用默认值: {e}")
                self._set_defaults()
                self.save_config()
        else:
            print("未找到配置文件，使用默认配置")
            self._set_defaults()
            self.save_config()
    
    def _set_defaults(self):
        """设置默认值"""
        for key, value in self.DEFAULTS.items():
            if key == "THRESHOLDS":
                self.THRESHOLDS = value.copy()
            else:
                setattr(self, key, value)
    
    def save_config(self):
        """保存配置到文件"""
        try:
            # 提取需要保存的属性
            data = {
                "MQTT_BROKER": self.MQTT_BROKER,
                "MQTT_PORT": self.MQTT_PORT,
                "MQTT_USER": self.MQTT_USER,
                "MQTT_PASSWORD": self.MQTT_PASSWORD,
                "AUTO_CAPTURE_ENABLED": self.AUTO_CAPTURE_ENABLED,
                "THRESHOLDS": self.THRESHOLDS
            }
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print("配置已保存到 system_config.json")
        except Exception as e:
            print(f"保存配置失败: {e}")
            
    # 以下是固定配置（不在设置界面修改）
    # MQTT主题
    MQTT_TOPICS = {
        "status": "esp32/main/status",
        "child_detection": "esp32cam/child_detection",
        "control": "python/control",
        "sensor_upload": "python/sensor_data",  # 新增：上位机上传传感器数据的主题
        "alerts": "python/alerts",  # 新增：报警同步发布的主题
        "captured_image": "python/captured_image", # 新增：抓拍图片传输主题
    }
    DEVICE_ID = "vehicle_monitor_001"  # 新增：设备ID
    
    # ESP32CAM配置
    ESP32CAM_IP = "192.168.235.31"
    ESP32CAM_STREAM_URL = f"http://{ESP32CAM_IP}/stream"
    
    # 串口备份配置
    SERIAL_PORT = "COM3"
    SERIAL_BAUD = 115200
    
    # 数据库配置
    DB_PATH = "vehicle_monitor.db"
    
    # 模型配置
    MODEL_PATH = r"D:\设计代码程序\best (2).pt"
    CONFIDENCE_THRESHOLD = 0.5
    
    # AIR780E短信报警配置（新增）
    AIR780E_CTRL_TOPIC = "python/air780e_control"

# ==================== 数据类 ====================
@dataclass
class SensorData:
    """传感器数据"""
    timestamp: float
    temperature: float
    humidity: float
    aqi: int
    tvoc: int
    eco2: int
    object_temp: float
    human_detected: bool
    child_detected: bool
    child_confidence: float
    door_closed: bool
    pir_state: bool
    adult_count: int = 0  # 添加成人数量
    child_count: int = 0  # 添加儿童数量
    
@dataclass
class DetectionResult:
    """检测结果"""
    timestamp: float
    child_detected: bool
    confidence: float
    bbox: List[int]
    child_count: int
    adult_count: int
    frame: Optional[np.ndarray] = None
    image_path: Optional[str] = None #新增：存储图片路径

@dataclass
class AlertInfo:
    """报警信息"""
    level: str  # "warning", "emergency"
    message: str
    timestamp: float
    confirmed: bool = False

# ==================== 数据库管理器 ====================
class DatabaseManager:
    """数据库管理"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 传感器数据表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
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
            pir_state INTEGER
        )
        ''')
        
        # 检测结果表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS detection_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            child_detected INTEGER,
            confidence REAL,
            bbox TEXT,
            child_count INTEGER,
            adult_count INTEGER
        )
        ''')
        
        # 报警记录表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            level TEXT,
            message TEXT,
            confirmed INTEGER DEFAULT 0
        )
        ''')
        
        # 系统事件表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event_type TEXT,
            event_data TEXT
        )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_sensor_data(self, data: SensorData):
        """保存传感器数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO sensor_data 
        (timestamp, temperature, humidity, aqi, tvoc, eco2, object_temp,
         human_detected, child_detected, child_confidence, door_closed,
         pir_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.timestamp,
            data.temperature,
            data.humidity,
            data.aqi,
            data.tvoc,
            data.eco2,
            data.object_temp,
            1 if data.human_detected else 0,
            1 if data.child_detected else 0,
            data.child_confidence,
            1 if data.door_closed else 0,
            1 if data.pir_state else 0
        ))
        
        conn.commit()
        conn.close()
    
    def save_detection_result(self, result: DetectionResult):
        """保存检测结果"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO detection_results 
        (timestamp, child_detected, confidence, bbox, child_count, adult_count)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            result.timestamp,
            1 if result.child_detected else 0,
            result.confidence,
            json.dumps(result.bbox),
            result.child_count,
            result.adult_count
        ))
        
        conn.commit()
        conn.close()
    
    def save_alert(self, alert: AlertInfo):
        """保存报警记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO alerts (timestamp, level, message, confirmed)
        VALUES (?, ?, ?, ?)
        ''', (
            alert.timestamp,
            alert.level,
            alert.message,
            1 if alert.confirmed else 0
        ))
        
        conn.commit()
        conn.close()
    
    def get_recent_data(self, limit: int = 100) -> pd.DataFrame:
        """获取最近数据"""
        conn = sqlite3.connect(self.db_path)
        query = '''
        SELECT * FROM sensor_data 
        ORDER BY timestamp DESC 
        LIMIT ?
        '''
        df = pd.read_sql_query(query, conn, params=(limit,))
        conn.close()
        return df
    
    def export_to_csv(self, table_name: str, filename: str):
        """导出数据到CSV"""
        conn = sqlite3.connect(self.db_path)
        query = f"SELECT * FROM {table_name}"
        df = pd.read_sql_query(query, conn)
        df.to_csv(filename, index=False)
        conn.close()

# ==================== 通信管理器（整合版+频率限制+独立发送线程） ====================
class CommunicationManager:
    """通信管理 - 优化版：添加频率限制和独立发送线程"""
    
    def __init__(self, config: Config):
        self.config = config
        unique_id = str(uuid.uuid4())[:8]
        self.mqtt_client = mqtt.Client(client_id=f"device_{unique_id}")
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_disconnect = self.on_disconnect
        self.mqtt_client.on_message = self.on_message
        self.data_queue = queue.Queue()
        self.is_mqtt_connected = False
        self.is_serial_connected = False
        self.serial_conn = None
        self.callbacks = []
        
        # ============ 新增：频率控制和独立发送线程 ============
        self.last_sensor_upload_time = 0
        self.sensor_upload_interval = 2.0  # 限制为每2秒上传一次
        self.sensor_data_buffer = []  # 缓冲池，用于存储待上传的数据
        self.send_queue = queue.Queue()
        self.send_thread = None
        self.running = True
        # ===================================================
        
        # 如果配置了用户名和密码，使用它们
        if config.MQTT_USER and config.MQTT_PASSWORD:
            self.mqtt_client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
        
        # 连接到MQTT服务器
        self.connect()
        
        # 尝试初始化串口（作为备份）
        self.init_serial()
        
        # 启动独立发送线程
        self.start_send_thread()
    
    def start_send_thread(self):
        """启动独立的MQTT发送线程"""
        self.send_thread = threading.Thread(target=self._send_worker, daemon=True)
        self.send_thread.start()
        print("MQTT发送线程已启动")
    
    def _send_worker(self):
        """MQTT发送工作线程"""
        while self.running:
            try:
                # 从队列获取数据，设置超时避免无限等待
                item = self.send_queue.get(timeout=1.0)
                
                # 处理不同类型的发送任务
                if isinstance(item, SensorData):
                    # 传感器数据，不做频率限制（由调用方控制）
                    self._send_sensor_data_now(item)
                    
                elif isinstance(item, tuple):
                    if len(item) == 2:
                        # (message, topic) 格式
                        msg, topic = item
                        payload = json.dumps(msg, ensure_ascii=False) if not isinstance(msg, str) else msg
                        self.mqtt_client.publish(topic, payload, qos=1)
                        
                    elif len(item) == 3:
                        # (message, topic, qos) 格式
                        msg, topic, qos = item
                        payload = json.dumps(msg, ensure_ascii=False) if not isinstance(msg, str) else msg
                        self.mqtt_client.publish(topic, payload, qos=qos)
                    
            except queue.Empty:
                continue
            except Exception as e:
                print(f"发送线程错误: {e}")
    
    def connect(self):
        """连接到MQTT服务器"""
        try:
            self.mqtt_client.connect(self.config.MQTT_BROKER, self.config.MQTT_PORT, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            print(f"MQTT连接失败: {e}")
    
    def init_serial(self):
        """初始化串口连接"""
        try:
            self.serial_conn = serial.Serial(
                port=self.config.SERIAL_PORT,
                baudrate=self.config.SERIAL_BAUD,
                timeout=1
            )
            self.is_serial_connected = True
            print(f"串口连接成功: {self.config.SERIAL_PORT}")
            
            # 启动串口读取线程
            serial_thread = threading.Thread(target=self._serial_read_loop, daemon=True)
            serial_thread.start()
        except Exception as e:
            print(f"串口连接失败: {e}")
            self.is_serial_connected = False
    
    def on_connect(self, client, userdata, flags, rc):
        """MQTT连接回调"""
        if rc == 0:
            self.is_mqtt_connected = True
            print("MQTT连接成功")
            
            # 订阅所有主题
            for topic in self.config.MQTT_TOPICS.values():
                client.subscribe(topic)
                print(f"已订阅主题: {topic}")
        else:
            print(f"MQTT连接失败，错误码: {rc}")
    
    def on_disconnect(self, client, userdata, rc):
        """MQTT断开连接回调"""
        self.is_mqtt_connected = False
        print(f"MQTT连接断开，错误码: {rc}")
    
    def on_message(self, client, userdata, msg):
        """MQTT消息回调"""
        try:
            payload = json.loads(msg.payload.decode())
            payload["topic"] = msg.topic
            payload["timestamp"] = time.time()
            
            # 放入队列
            self.data_queue.put(payload)
            
            # 调用回调函数
            for callback in self.callbacks:
                callback(payload)
                
        except Exception as e:
            print(f"MQTT消息处理错误: {e}")
    
    def _serial_read_loop(self):
        """串口读取循环"""
        while self.is_serial_connected:
            try:
                if self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode('utf-8').strip()
                    if line:
                        try:
                            data = json.loads(line)
                            data["source"] = "serial"
                            data["timestamp"] = time.time()
                            self.data_queue.put(data)
                        except:
                            pass
            except:
                pass
            time.sleep(0.01)
    
    def send_control_command(self, command: str, params: dict = None):
        """发送控制命令"""
        message = {
            "command": command,
            "params": params or {},
            "timestamp": time.time()
        }
        
        # 优先使用MQTT
        if self.is_mqtt_connected:
            # 使用发送队列，避免阻塞
            self.send_queue.put((message, self.config.MQTT_TOPICS["control"]))
            return True
        # 备用串口
        elif self.is_serial_connected:
            try:
                self.serial_conn.write(json.dumps(message).encode())
                return True
            except:
                return False
        
        return False
    
    def send_detection_message(self, child_detected, confidence, bbox=None, child_count=0, adult_count=0):
        """发送检测结果MQTT消息"""
        try:
            message = {
                "timestamp": time.time(),
                "child_detected": child_detected,
                "confidence": round(float(confidence), 3) if confidence else 0,
                "bbox": bbox if bbox else [],
                "child_count": child_count,
                "adult_count": adult_count,
                "total_count": child_count + adult_count,
                "device_id": self.config.DEVICE_ID,
                "frame_time": time.strftime("%Y-%m-d %H:%M:%S")
            }
            # 使用发送队列，避免阻塞
            self.send_queue.put((message, self.config.MQTT_TOPICS["child_detection"]))
            
            if child_detected:
                print(f"检测消息已加入发送队列: 检测到儿童 (置信度: {confidence:.2f})")
            return True
        except Exception as e:
            print(f"发送检测消息错误: {e}")
            return False
    
    # 新增：发送短信命令
    def send_sms_command(self, message: str = None):
        """发送短信命令到AIR780E模块"""
        try:
            cmd = {
                "command": "send_sms",
                "time": time.time()
            }
            
            # 如果提供了自定义消息，添加到命令中
            if message:
                cmd["message"] = message
                
            # 使用发送队列，避免阻塞
            self.send_queue.put((cmd, self.config.MQTT_TOPICS["control"]))
            print(f"短信指令已加入发送队列: {message[:50] if message else '默认短信'}")
            return True
        except Exception as e:
            print(f"发送短信指令失败: {e}")
            return False
    
    def register_callback(self, callback):
        """注册数据回调函数"""
        self.callbacks.append(callback)
    
    def get_data(self, timeout: float = 0.1):
        """从队列获取数据"""
        try:
            return self.data_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def send_sensor_data(self, sensor_data: SensorData) -> bool:
        """上传传感器数据到MQTT（带频率限制）"""
        current_time = time.time()
        
        # 检查是否达到上传间隔
        if current_time - self.last_sensor_upload_time < self.sensor_upload_interval:
            # 将数据存入缓冲池，稍后发送
            self.sensor_data_buffer.append(sensor_data)
            return True  # 返回True表示数据已接收，但不是立即发送
        
        try:
            # 如果有缓冲数据，可以选择合并或只发送最新的一条
            if self.sensor_data_buffer:
                # 合并缓冲数据（取平均值或最新值）
                combined_data = self.combine_buffered_data(sensor_data)
                message = asdict(combined_data)
            else:
                message = asdict(sensor_data)
                
            message["device_id"] = self.config.DEVICE_ID
            message["frame_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            message["buffered_count"] = len(self.sensor_data_buffer)  # 可选：包含缓冲数量信息
            
            # 使用发送队列，避免阻塞
            self.send_queue.put((message, self.config.MQTT_TOPICS["sensor_upload"]))
            
            self.last_sensor_upload_time = current_time
            buffered_count = len(self.sensor_data_buffer)
            self.sensor_data_buffer = []  # 清空缓冲区
            print(f"传感器数据已加入发送队列: {sensor_data.temperature}°C, 缓冲数据: {buffered_count}条")
            return True
        except Exception as e:
            print(f"上传传感器数据错误: {e}")
            return False
    
    def combine_buffered_data(self, latest_data: SensorData) -> SensorData:
        """合并缓冲数据（取最新值）"""
        if not self.sensor_data_buffer:
            return latest_data
        
        # 简单实现：返回最新的数据（也可以实现平均值、最大值等）
        return latest_data
    
    def _send_sensor_data_now(self, sensor_data: SensorData):
        """立即发送传感器数据（在线程中调用）"""
        try:
            message = asdict(sensor_data)
            message["device_id"] = self.config.DEVICE_ID
            message["frame_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            
            payload = json.dumps(message, ensure_ascii=False)
            result = self.mqtt_client.publish(
                self.config.MQTT_TOPICS["sensor_upload"], 
                payload, 
                qos=0  # 降低QoS级别，加快发送速度
            )
            
            # 不需要等待结果，直接继续
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"MQTT发送返回错误: {result.rc}")
                
        except Exception as e:
            print(f"发送数据错误: {e}")
    
    def publish(self, topic: str, payload: str, qos=0):
        """简单发布方法"""
        if self.is_mqtt_connected:
            # 使用发送队列，避免阻塞
            self.send_queue.put((payload, topic, qos))
            return True
        return False
    
    def stop(self):
        """停止通信管理器"""
        self.running = False
        if self.send_thread:
            self.send_thread.join(timeout=2.0)
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

# ==================== 视觉识别管理器（整合版） ====================
class VisionManager:
    """视觉识别管理"""
    
    def __init__(self, config: Config):
        self.config = config
        self.model = None
        self.stream_url = config.ESP32CAM_STREAM_URL
        self.is_running = False
        self.frame_queue = queue.Queue(maxsize=10)
        self.detection_queue = queue.Queue(maxsize=10)
        self.current_frame = None
        self.current_detections = []
        self.class_names = ['0', 'adult', 'kids']  # 类别名称
        self.CHILD_CLASS_ID = 2  # 儿童类别ID
        self.ADULT_CLASS_ID = 1  # 成人类别ID
        self.DETECTION_INTERVAL = 0.5  # 检测间隔（秒）
        self.last_detection_time = 0
        self.comm_manager = None  # 稍后关联通信管理器
        self.cap = None
        
        # ============ 新增：抓拍相关变量 ============
        self.last_capture_time = 0
        self.capture_interval = 60.0  # 抓拍间隔（秒）
        self.person_detected_flag = False  # 跟踪是否连续检测到人员
        self.capture_conf_threshold = 0.6  # 抓拍置信度阈值
        # ==========================================
        
        # 加载模型
        self.load_model()
    
    def load_model(self):
        """加载YOLO模型"""
        try:
            print("正在加载YOLOv5模型...")
            self.model = torch.hub.load('ultralytics/yolov5', 'custom',
                                       path=self.config.MODEL_PATH, force_reload=False,
                                       trust_repo=True)
            self.model.conf = self.config.CONFIDENCE_THRESHOLD
            self.model.iou = 0.45
            self.model.eval()
            print(f"模型加载成功: {self.model.names}")
        except Exception as e:
            print(f"模型加载失败: {e}")
            print("建议：")
            print("  - 检查模型路径是否正确")
            print("  - pip install ultralytics")
            self.model = None
    
    def start_stream(self):
        """启动视频流和检测线程"""
        if self.is_running:
            return
        
        self.is_running = True
        
        # 启动视频流线程（使用优化读取方式）
        stream_thread = threading.Thread(target=self._stream_worker, daemon=True)
        stream_thread.start()
        
        # 启动检测线程
        detect_thread = threading.Thread(target=self._detect_worker, daemon=True)
        detect_thread.start()
    
    def stop_stream(self):
        """停止视频流"""
        self.is_running = False
        if self.cap:
            self.cap.release()
    
    def _stream_worker(self):
        """视频流工作线程，采用优化读取方式"""
        import requests
        from requests.exceptions import RequestException
        
        while self.is_running:
            try:
                print(f"尝试连接 ESP32-CAM 视频流: {self.stream_url}")
                response = requests.get(self.stream_url, stream=True, timeout=15)
                
                if response.status_code == 200:
                    print("✓ 视频流连接成功")
                    bytes_data = b''
                    
                    while self.is_running:
                        try:
                            chunk = response.raw.read(2048)  # 增大读取块大小
                            if not chunk:
                                print("流数据中断，准备重连...")
                                break
                                
                            bytes_data += chunk
                            a = bytes_data.find(b'\xff\xd8')  # JPEG开始
                            b = bytes_data.find(b'\xff\xd9')  # JPEG结束
                            
                            if a != -1 and b != -1 and b > a:
                                jpeg_data = bytes_data[a:b+2]
                                bytes_data = bytes_data[b+2:]
                                
                                # 解码JPEG数据
                                frame = cv2.imdecode(np.frombuffer(jpeg_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                                
                                if frame is not None and frame.size > 0:
                                    # 限制队列大小
                                    if self.frame_queue.qsize() >= 10:
                                        try:
                                            self.frame_queue.get_nowait()
                                        except:
                                            pass
                                    
                                    try:
                                        self.frame_queue.put_nowait(frame)
                                        self.current_frame = frame
                                    except:
                                        pass
                                
                        except Exception as e:
                            print(f"流读取错误: {e}")
                            break
                            
                    response.close()
                else:
                    print(f"HTTP错误: {response.status_code}")
                    
            except RequestException as e:
                print(f"连接失败: {str(e)[:30]}")
            except Exception as e:
                print(f"流异常: {str(e)[:30]}")

            if self.is_running:
                print(f"5秒后重新连接...")
                time.sleep(5)
    
    def _detect_worker(self):
        """检测工作线程，使用优化的检测逻辑"""
        while self.is_running:
            try:
                frame = self.frame_queue.get(timeout=0.1)
                current_time = time.time()
                
                if frame is not None and self.model is not None:
                    # 限制检测频率
                    if current_time - self.last_detection_time >= self.DETECTION_INTERVAL:
                        # 执行检测
                        detections = self.detect_objects(frame)
                        self.current_detections = detections
                        self.last_detection_time = current_time
                        
                        # 统计儿童和成人数量
                        child_count = sum(1 for d in detections if d["class"] == self.CHILD_CLASS_ID)
                        adult_count = sum(1 for d in detections if d["class"] == self.ADULT_CLASS_ID)
                        
                        # 计算最大置信度
                        max_conf = max([d["confidence"] for d in detections]) if detections else 0.0
                        
                        detection_result = DetectionResult(
                            timestamp=time.time(),
                            child_detected=child_count > 0,
                            confidence=max_conf,
                            bbox=detections[0]["bbox"] if detections else [],
                            child_count=child_count,
                            adult_count=adult_count,
                            frame=frame.copy() # 保存当前帧
                        )
                        
                        # 放入检测队列
                        if self.detection_queue.qsize() >= 5:
                            try:
                                self.detection_queue.get_nowait()
                            except:
                                pass
                        self.detection_queue.put(detection_result)
                        
                        # ============ 新增：抓拍逻辑（受全局开关控制） ============
                        person_detected = (child_count > 0 or adult_count > 0) and max_conf > self.capture_conf_threshold
                        
                        # 只有当自动抓拍开关开启时才执行抓拍
                        if self.config.AUTO_CAPTURE_ENABLED:
                            # 首次检测到人员：无论儿童还是成人，都立即抓拍（及时通知）
                            if person_detected and not self.person_detected_flag:
                                self.capture_and_send(frame, child_count, adult_count, max_conf)
                                self.person_detected_flag = True
                            
                            # 连续检测时：只有检测到儿童，才每隔 capture_interval 秒抓拍一次
                            elif person_detected and child_count > 0 and (current_time - self.last_capture_time >= self.capture_interval):
                                self.capture_and_send(frame, child_count, adult_count, max_conf)
                            
                            # 如果检测到成人但无儿童，不进行连续抓拍（节省流量）
                            
                            elif not person_detected:
                                # 人员消失，重置标志
                                self.person_detected_flag = False
                        else:
                            # 开关关闭时，仅重置标志，防止开启后立即触发
                            if not person_detected:
                                self.person_detected_flag = False
                        # ============================================================
                       
                        # 如果有通信管理器，发送检测结果
                        if self.comm_manager:
                            self.comm_manager.send_detection_message(
                                child_detected=child_count > 0,
                                confidence=detection_result.confidence,
                                bbox=detection_result.bbox,
                                child_count=child_count,
                                adult_count=adult_count
                            )
                        
            except queue.Empty:
                continue
            except Exception as e:
                print(f"检测错误: {e}")
    
    def detect_objects(self, frame):
        """检测所有对象（成人和儿童）"""
        if self.model is None:
            return []
        try:
            # 如果帧太大，先调整尺寸提高检测速度
            if frame.shape[1] > 640:
                scale_factor = 640 / frame.shape[1]
                new_width = 640
                new_height = int(frame.shape[0] * scale_factor)
                frame_resized = cv2.resize(frame, (new_width, new_height))
            else:
                frame_resized = frame
                
            rgb_frame = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
            results = self.model(rgb_frame)
            detections = results.xyxy[0].cpu().numpy()

            all_detections = []
            for det in detections:
                x1, y1, x2, y2, conf, cls = det
                if conf < self.config.CONFIDENCE_THRESHOLD:
                    continue
                class_id = int(cls)
                
                # 如果调整了尺寸，需要将坐标映射回原图
                if frame.shape[1] > 640:
                    scale_x = frame.shape[1] / new_width
                    scale_y = frame.shape[0] / new_height
                    x1, x2 = int(x1 * scale_x), int(x2 * scale_x)
                    y1, y2 = int(y1 * scale_y), int(y2 * scale_y)
                else:
                    x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
                    
                # 确保坐标在图像范围内
                h, w = frame.shape[:2]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                
                # 获取类别名称
                class_name = self.class_names[class_id] if class_id < len(self.class_names) else f"class_{class_id}"
                
                all_detections.append({
                    "bbox": [x1, y1, x2, y2],
                    "confidence": float(conf),
                    "class": class_id,
                    "class_name": class_name
                })
            return all_detections
        except Exception as e:
            print(f"检测出错: {e}")
            return []
    
    def get_frame_with_detections(self):
        """获取带检测框的帧"""
        if self.current_frame is None:
            return None
        
        frame = self.current_frame.copy()
        
        for det in self.current_detections:
            x1, y1, x2, y2 = det["bbox"]
            conf = det["confidence"]
            class_name = det["class_name"]
            
            # 选择颜色（儿童用绿色，成人用蓝色，其他用红色）
            if class_name.lower() in ['kids', 'child', 'children']:
                color = (0, 255, 0)  # 绿色
            elif class_name.lower() in ['adult', 'adults']:
                color = (255, 0, 0)  # 蓝色
            else:
                color = (0, 0, 255)  # 红色
            
            # 绘制边界框
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            # 绘制标签背景
            label = f"{class_name} {conf:.2f}"
            label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
            cv2.rectangle(frame, (x1, y1 - label_size[1] - 10), 
                         (x1 + label_size[0], y1), color, -1)
            
            # 绘制标签文本
            cv2.putText(frame, label, (x1, y1 - 5), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        
        return frame
    
    def get_detection_result(self):
        """获取检测结果"""
        try:
            return self.detection_queue.get_nowait()
        except queue.Empty:
            return None
    
    def reset(self):
        """重置视觉管理器状态"""
        # 清除当前的检测数据
        self.current_detections = []
        # 清空队列
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except:
                pass
        while not self.detection_queue.empty():
            try:
                self.detection_queue.get_nowait()
            except:
                pass
        self.current_frame = None
        
        # 重置抓拍状态
        self.last_capture_time = 0
        self.person_detected_flag = False
        
        print("视觉管理器已重置")

    def capture_and_send(self, frame: np.ndarray, child_count: int, adult_count: int, confidence: float):
        """抓拍并发送图片"""
        try:
            current_time = time.time()
            
            # 可选：绘制边界框到frame（使用现有draw_detections逻辑）
            frame_with_boxes = self.draw_detections(frame.copy(), self.current_detections)
            
            # 压缩图片
            _, buffer = cv2.imencode('.jpg', frame_with_boxes, [cv2.IMWRITE_JPEG_QUALITY, 80])
            image_base64 = base64.b64encode(buffer).decode('utf-8')
            
            # 确定检测类型
            if child_count > 0 and adult_count > 0:
                det_type = "both"
            elif child_count > 0:
                det_type = "child"
            elif adult_count > 0:
                det_type = "adult"
            else:
                det_type = "none"
            
            # 构建消息
            message = {
                "timestamp": current_time,
                "image_base64": image_base64,
                "detection_type": det_type,
                "child_count": child_count,
                "adult_count": adult_count,
                "confidence": round(confidence, 3),
                "device_id": self.config.DEVICE_ID
            }
            
            # 发送到MQTT（使用发送队列）
            if self.comm_manager:
                self.comm_manager.send_queue.put((message, self.config.MQTT_TOPICS["captured_image"], 1))
                print(f"抓拍图片已发送: 类型={det_type}, 置信度={confidence}")
            
            self.last_capture_time = current_time
        except Exception as e:
            print(f"抓拍发送错误: {e}")
    
    def draw_detections(self, frame: np.ndarray, detections: list) -> np.ndarray:
        """在帧上绘制检测框"""
        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            conf = det["confidence"]
            class_name = det["class_name"]
            
            # 选择颜色（儿童用绿色，成人用蓝色，其他用红色）
            if class_name.lower() in ['kids', 'child', 'children']:
                color = (0, 255, 0)  # 绿色
            elif class_name.lower() in ['adult', 'adults']:
                color = (255, 0, 0)  # 蓝色
            else:
                color = (0, 0, 255)  # 红色
            
            # 绘制边界框
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            # 绘制标签背景
            label = f"{class_name} {conf:.2f}"
            label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
            cv2.rectangle(frame, (x1, y1 - label_size[1] - 10), 
                         (x1 + label_size[0], y1), color, -1)
            
            # 绘制标签文本
            cv2.putText(frame, label, (x1, y1 - 5), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        
        return frame

# ==================== 通知管理器（删除邮件功能，保留短信，添加报警同步发布） ====================
class NotificationManager:
    """通知管理"""
    
    def __init__(self, config: Config, db_manager: DatabaseManager, comm_manager: CommunicationManager):
        self.config = config
        self.db_manager = db_manager
        self.comm_manager = comm_manager
        self.alerts = []
        self.last_sms_time = 0
        self.sms_cooldown = 300  # 短信冷却时间：5分钟（防止频繁发送）
    
    def create_alert(self, level: str, message: str):
        """创建警报"""
        alert = AlertInfo(
            level=level,
            message=message,
            timestamp=time.time()
        )
        self.alerts.append(alert)
        self.db_manager.save_alert(alert)

        # === 新增：同步发布到远控端 ===
        alert_payload = {
            "timestamp": alert.timestamp,
            "level": alert.level,
            "message": alert.message,
            "device_id": self.config.DEVICE_ID
        }
        json_payload = json.dumps(alert_payload, ensure_ascii=False)
        success = self.comm_manager.publish(self.config.MQTT_TOPICS["alerts"], json_payload)
        
        if success:
            print(f"【报警同步】已发布到远控端: {level} - {message}")
        else:
            print("【报警同步】发布失败（MQTT未连接）")

        # 如果是紧急警报，发送短信
        if level == "emergency":
            self.send_emergency_sms(alert)
    
    def send_emergency_sms(self, alert: AlertInfo):
        """发送紧急短信"""
        current_time = time.time()
        
        # 检查冷却时间
        if current_time - self.last_sms_time < self.sms_cooldown:
            # 冷却中不打印（避免频繁提示）
            return False
        
        # 发送短信，使用下位机默认内容
        success = self.comm_manager.send_sms_command()
        
        if success:
            self.last_sms_time = current_time
            print("🚨 已触发紧急短信报警")  # 关键动作：只打印一次
            
            # 记录短信发送事件
            self.db_manager.save_alert(AlertInfo(
                level="info",
                message="已发送紧急短信",
                timestamp=current_time
            ))
            
        return success
    
    def reset(self):
        """重置通知管理器状态，包括冷却时间"""
        # 重置短信发送冷却时间
        self.last_sms_time = 0
        # 保留警报历史不清除（可根据需要调整）
        print("通知管理器已重置，短信冷却时间已清零")
    
    def get_recent_alerts(self, n: int = 10) -> List[AlertInfo]:
        """获取最近警报"""
        return self.alerts[-n:] if self.alerts else []
    
    # 保留v1.0的额外功能
    def send_sms(self, message: str, phone_number: str):
        """发送短信通知（需要对接短信服务商）"""
        # 这里需要集成短信服务商API
        print(f"[模拟] 发送短信到 {phone_number}: {message}")
        return True

# ==================== 风险评估引擎（修改版：增强玩偶误判防护 + 明确warning和emergency区分 + 返回动作标志） ====================
class RiskEngine:
    """风险评估"""
    
    def __init__(self, config: Config):
        self.config = config
        self.risk_level = "normal"  # normal, warning, emergency
        self.last_assessment_time = 0
        self.assessment_interval = 2.0  # 每2秒评估一次
        self.last_window_action_time = 0  # 上次降窗时间
        self.window_cooldown = 300  # 降窗冷却时间：5分钟
        
        # 新增：防止玩偶误判的状态跟踪
        self.person_confidence = 0  # 0-100的人员置信度
        self.false_positive_history = []  # 误判历史记录
        self.consecutive_detections = 0  # 连续检测到人员的帧数
    
    def assess_risk(self, sensor_data: SensorData, detection_result: DetectionResult = None) -> Tuple[str, List[str], bool, bool, bool]:
        """评估风险级别，返回(风险级别, 原因列表, 需要报警, 需要降窗, 需要短信)"""
        current_time = time.time()
        
        # 控制评估频率
        if current_time - self.last_assessment_time < self.assessment_interval:
            return self.risk_level, [], False, False, False
        
        self.last_assessment_time = current_time
        
        reasons = []
        risk_factors = []
        need_auto_window = False
        need_alarm = False
        need_sms = False
        
        # 重要：只有在车门关闭的情况下才评估自动降窗
        if not sensor_data.door_closed:
            # 车门打开时，系统应处于安全状态
            self.risk_level = "normal"
            self.person_confidence = 0
            self.consecutive_detections = 0
            return self.risk_level, [], False, False, False
        
        # ============ 新增：玩偶误判防护逻辑 ============
        
        # 计算当前的人员置信度（0-100）
        current_confidence = self.calculate_person_confidence(sensor_data, detection_result)
        
        # 更新连续检测计数器
        if current_confidence >= 50:  # 置信度超过50%认为可能有人
            self.consecutive_detections += 1
        else:
            self.consecutive_detections = max(0, self.consecutive_detections - 2)
        
        # 判断是否确认有人（需要高置信度或连续多次检测）
        person_confirmed = False
        
        # 条件1：高置信度（>70%）且连续检测到（>=3帧）
        if current_confidence >= 70 and self.consecutive_detections >= 3:
            person_confirmed = True
            reasons.append("确认有人员（多重验证通过）")
            
        # 条件2：极高置信度（>85%），不需要连续检测
        elif current_confidence >= 85:
            person_confirmed = True
            reasons.append("确认有人员（传感器高度一致）")
            
        # 条件3：低置信度但连续多次检测（可能是YOLO漏检，但传感器持续检测到）
        elif current_confidence >= 40 and self.consecutive_detections >= 10:
            person_confirmed = True
            reasons.append("确认有人员（持续检测确认）")
        
        # 如果是低置信度的YOLO检测（可能是玩偶）
        elif detection_result and (detection_result.child_count > 0 or detection_result.adult_count > 0) and current_confidence < 40:
            reasons.append(f"检测到目标但置信度低({current_confidence}%)，可能是玩偶")
            self.record_false_positive(current_time, "low_confidence_detection")
        
        # ============ 明确区分warning和emergency的判断逻辑 ============
        
        # 1. 检查极端条件（emergency级别 - 无论是否有人）
        extreme_conditions = []
        
        if sensor_data.temperature > self.config.THRESHOLDS["temperature_extreme"]:
            extreme_conditions.append(f"极端高温({sensor_data.temperature:.1f}°C)")
            need_auto_window = True
            need_alarm = True
            need_sms = True
        
        if sensor_data.eco2 > self.config.THRESHOLDS["co2_extreme"]:
            extreme_conditions.append(f"极端高CO2({sensor_data.eco2}ppm)")
            need_auto_window = True
            need_alarm = True
            need_sms = True
        
        if sensor_data.tvoc > self.config.THRESHOLDS["tvoc_extreme"]:
            extreme_conditions.append(f"极端高TVOC({sensor_data.tvoc}ppb)")
            need_auto_window = True
            need_alarm = True
            need_sms = True
        
        # 如果有极端条件，直接返回emergency
        if extreme_conditions:
            reasons.extend(extreme_conditions)
            self.risk_level = "emergency"
            self.person_confidence = current_confidence
            return self.risk_level, reasons, need_alarm, need_auto_window, need_sms
        
        # 2. 如果没有人，则不需要自动降窗，检查是否warning
        if not person_confirmed:
            # 检查警告条件（环境参数超过警告阈值但未达到极端阈值）
            warning_conditions = []
            
            if sensor_data.temperature > self.config.THRESHOLDS["temperature_high"]:
                warning_conditions.append(f"高温({sensor_data.temperature:.1f}°C)")
                need_alarm = True
            
            if sensor_data.humidity > self.config.THRESHOLDS["humidity_high"]:
                warning_conditions.append(f"高湿度({sensor_data.humidity:.1f}%)")
                need_alarm = True
            
            if sensor_data.eco2 > self.config.THRESHOLDS["co2_high"]:
                warning_conditions.append(f"高CO2({sensor_data.eco2}ppm)")
                need_alarm = True
            
            if sensor_data.tvoc > self.config.THRESHOLDS["tvoc_high"]:
                warning_conditions.append(f"高TVOC({sensor_data.tvoc}ppb)")
                need_alarm = True
            
            if warning_conditions:
                reasons.extend(warning_conditions)
                self.risk_level = "warning"
            else:
                self.risk_level = "normal"
            
            self.person_confidence = current_confidence
            return self.risk_level, reasons, need_alarm, False, False
        
        # 到这里，说明车门关闭且确认有人员
        # 3. 检查警告条件（warning级别 - 有人但环境参数超过警告阈值）
        warning_conditions = []
        
        if sensor_data.temperature > self.config.THRESHOLDS["temperature_high"]:
            warning_conditions.append(f"高温({sensor_data.temperature:.1f}°C)")
            need_auto_window = True  # 有人时，高温需要自动降窗
            need_alarm = True
            need_sms = True
        
        if sensor_data.humidity > self.config.THRESHOLDS["humidity_high"]:
            warning_conditions.append(f"高湿度({sensor_data.humidity:.1f}%)")
            need_alarm = True
            # 湿度高但可能不需要自动降窗，除非温度也高
        
        if sensor_data.eco2 > self.config.THRESHOLDS["co2_high"]:
            warning_conditions.append(f"高CO2({sensor_data.eco2}ppm)")
            need_auto_window = True  # 有人时，高CO2需要自动降窗
            need_alarm = True
            need_sms = True
        
        if sensor_data.tvoc > self.config.THRESHOLDS["tvoc_high"]:
            warning_conditions.append(f"高TVOC({sensor_data.tvoc}ppb)")
            need_auto_window = True  # 有人时，高TVOC需要自动降窗
            need_alarm = True
            need_sms = True
        
        # 4. 判断风险等级
        if need_auto_window:
            # 如果触发自动降窗条件，设置为紧急（因为有人且环境危险）
            self.risk_level = "emergency"
            reasons.extend(warning_conditions)
        elif warning_conditions:
            # 有警告条件但未触发自动降窗，设为警告
            self.risk_level = "warning"
            reasons.extend(warning_conditions)
        else:
            # 只有人员检测，环境正常
            self.risk_level = "normal"
        
        self.person_confidence = current_confidence
        return self.risk_level, reasons, need_alarm, need_auto_window, need_sms
    
    def calculate_person_confidence(self, sensor_data: SensorData, detection_result: DetectionResult = None) -> int:
        """计算人员置信度（0-100）"""
        confidence = 0
        
        # 1. YOLO视觉检测（权重：40分）
        if detection_result:
            # 检测到儿童或成人
            if detection_result.child_count > 0 or detection_result.adult_count > 0:
                confidence += 40
                
                # 高置信度的检测额外加分
                if detection_result.confidence > 0.8:
                    confidence += 10
                elif detection_result.confidence > 0.6:
                    confidence += 5
                
                # 多人检测更可信
                total_people = detection_result.child_count + detection_result.adult_count
                if total_people > 1:
                    confidence += 5
        
        # 2. MLX90614人体温度检测（权重：30分）
        if sensor_data.human_detected:
            confidence += 30
            
            # 人体温度在合理范围内额外加分
            if 30.0 <= sensor_data.object_temp <= 40.0:
                confidence += 10
            elif 20.0 <= sensor_data.object_temp <= 50.0:
                confidence += 5
        
        # 3. PIR运动检测（权重：30分）
        if sensor_data.pir_state:
            confidence += 30
            
            # 如果PIR检测到运动，但MLX90614没有检测到人体温度，可能是误报（小动物）
            if not sensor_data.human_detected:
                confidence -= 10  # 降低置信度
        
        # 4. 组合验证加分
        # 如果YOLO和至少一个传感器同时触发，额外加分
        if detection_result and (detection_result.child_count > 0 or detection_result.adult_count > 0):
            if sensor_data.human_detected or sensor_data.pir_state:
                confidence += 15
        
        # 如果三个传感器都触发，高度可信
        if (detection_result and (detection_result.child_count > 0 or detection_result.adult_count > 0) and
            sensor_data.human_detected and sensor_data.pir_state):
            confidence += 20
        
        # 限制在0-100范围内
        return max(0, min(100, confidence))
    
    def record_false_positive(self, timestamp: float, reason: str):
        """记录可能的误判事件"""
        self.false_positive_history.append({
            "timestamp": timestamp,
            "reason": reason,
            "person_confidence": self.person_confidence
        })
        
        # 只保留最近的20条记录
        if len(self.false_positive_history) > 20:
            self.false_positive_history.pop(0)
    
    def should_auto_window(self, current_time: float) -> bool:
        """判断是否需要执行自动降窗（考虑冷却时间）"""
        if current_time - self.last_window_action_time > self.window_cooldown:
            self.last_window_action_time = current_time
            return True
        return False
    
    def reset(self):
        """重置风险评估引擎状态"""
        self.risk_level = "normal"
        self.last_assessment_time = 0
        self.last_window_action_time = 0  # 重置自动降窗冷却时间
        self.person_confidence = 0
        self.false_positive_history = []
        self.consecutive_detections = 0
        print("风险评估引擎已重置，冷却时间已清零")
    
    def get_risk_description(self, level: str, reasons: List[str] = None) -> str:
        """获取风险描述"""
        if level == "normal":
            if self.person_confidence > 30:
                return f"系统正常（人员置信度：{self.person_confidence}%）"
            return "系统正常"
        elif level == "warning":
            if reasons:
                reason_text = ', '.join(reasons)
                # 区分warning的不同类型
                if any("高温" in r for r in reasons) or any("CO2" in r for r in reasons) or any("TVOC" in r for r in reasons):
                    return f"⚠️ 环境警告: {reason_text}（人员置信度：{self.person_confidence}%）"
                else:
                    return f"⚠️ 系统警告: {reason_text}（人员置信度：{self.person_confidence}%）"
            else:
                return f"⚠️ 警告：存在潜在风险（人员置信度：{self.person_confidence}%）"
        else:  # emergency
            if reasons:
                reason_text = ', '.join(reasons)
                # 区分emergency的不同类型
                if any("极端" in r for r in reasons):
                    return f"🚨 环境紧急: {reason_text} 自动降窗已触发!（人员置信度：{self.person_confidence}%）"
                else:
                    return f"🚨 人员紧急: {reason_text} 自动降窗已触发!（人员置信度：{self.person_confidence}%）"
            else:
                return f"🚨 紧急风险！自动降窗已触发!（人员置信度：{self.person_confidence}%）"
    
    def get_detection_summary(self) -> dict:
        """获取检测摘要"""
        return {
            "person_confidence": self.person_confidence,
            "consecutive_detections": self.consecutive_detections,
            "false_positive_count": len(self.false_positive_history),
            "recent_false_positives": self.false_positive_history[-5:] if self.false_positive_history else []
        }

# ==================== 模拟测试窗口（新增） ====================
class SimulationWindow(QDialog):
    """模拟测试窗口"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent  # 保存父窗口引用
        self.setWindowTitle("模拟测试面板")
        self.setGeometry(300, 300, 600, 800)
        
        # 模拟数据
        self.simulated_data = {
            "temperature": 25.0,
            "humidity": 50.0,
            "eco2": 400,
            "tvoc": 50,
            "door_closed": True,
            "human_detected": False,
            "child_detected": False,
            "object_temp": 30.0,
            "pir_state": False,
            "child_count": 0,
            "adult_count": 0,
            "confidence": 0.0
        }
        
        self.init_ui()
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # 创建滚动区域
        scroll_area = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_widget.setLayout(scroll_layout)
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        layout.addWidget(scroll_area)
        
        # 温度模拟
        temp_group = QGroupBox("温度模拟")
        temp_layout = QVBoxLayout()
        
        self.temp_slider = QSlider(Qt.Horizontal)
        self.temp_slider.setRange(0, 500)  # 0-50.0°C
        self.temp_slider.setValue(250)  # 25.0°C
        self.temp_slider.valueChanged.connect(self.update_temp_label)
        
        self.temp_label = QLabel("温度: 25.0°C")
        
        temp_layout.addWidget(self.temp_label)
        temp_layout.addWidget(self.temp_slider)
        temp_group.setLayout(temp_layout)
        scroll_layout.addWidget(temp_group)
        
        # 湿度模拟
        humid_group = QGroupBox("湿度模拟")
        humid_layout = QVBoxLayout()
        
        self.humid_slider = QSlider(Qt.Horizontal)
        self.humid_slider.setRange(0, 1000)  # 0-100.0%
        self.humid_slider.setValue(500)  # 50.0%
        self.humid_slider.valueChanged.connect(self.update_humid_label)
        
        self.humid_label = QLabel("湿度: 50.0%")
        
        humid_layout.addWidget(self.humid_label)
        humid_layout.addWidget(self.humid_slider)
        humid_group.setLayout(humid_layout)
        scroll_layout.addWidget(humid_group)
        
        # CO2模拟
        co2_group = QGroupBox("CO2浓度模拟")
        co2_layout = QVBoxLayout()
        
        self.co2_slider = QSlider(Qt.Horizontal)
        self.co2_slider.setRange(300, 2000)  # 300-2000ppm
        self.co2_slider.setValue(400)  # 400ppm
        self.co2_slider.valueChanged.connect(self.update_co2_label)
        
        self.co2_label = QLabel("CO2浓度: 400ppm")
        
        co2_layout.addWidget(self.co2_label)
        co2_layout.addWidget(self.co2_slider)
        co2_group.setLayout(co2_layout)
        scroll_layout.addWidget(co2_group)
        
        # TVOC模拟
        tvoc_group = QGroupBox("TVOC浓度模拟")
        tvoc_layout = QVBoxLayout()
        
        self.tvoc_slider = QSlider(Qt.Horizontal)
        self.tvoc_slider.setRange(0, 1500)  # 0-1500ppb
        self.tvoc_slider.setValue(50)  # 50ppb
        self.tvoc_slider.valueChanged.connect(self.update_tvoc_label)
        
        self.tvoc_label = QLabel("TVOC浓度: 50ppb")
        
        tvoc_layout.addWidget(self.tvoc_label)
        tvoc_layout.addWidget(self.tvoc_slider)
        tvoc_group.setLayout(tvoc_layout)
        scroll_layout.addWidget(tvoc_group)
        
        # 人体温度模拟
        object_temp_group = QGroupBox("人体温度模拟")
        object_temp_layout = QVBoxLayout()
        
        self.object_temp_slider = QSlider(Qt.Horizontal)
        self.object_temp_slider.setRange(200, 500)  # 20.0-50.0°C
        self.object_temp_slider.setValue(300)  # 30.0°C
        self.object_temp_slider.valueChanged.connect(self.update_object_temp_label)
        
        self.object_temp_label = QLabel("人体温度: 30.0°C")
        
        object_temp_layout.addWidget(self.object_temp_label)
        object_temp_layout.addWidget(self.object_temp_slider)
        object_temp_group.setLayout(object_temp_layout)
        scroll_layout.addWidget(object_temp_group)
        
        # 车门状态
        door_group = QGroupBox("车门状态模拟")
        door_layout = QHBoxLayout()
        
        self.door_open_radio = QRadioButton("车门打开")
        self.door_closed_radio = QRadioButton("车门关闭")
        self.door_closed_radio.setChecked(True)
        self.door_open_radio.toggled.connect(self.update_door_state)
        self.door_closed_radio.toggled.connect(self.update_door_state)
        
        door_layout.addWidget(self.door_open_radio)
        door_layout.addWidget(self.door_closed_radio)
        door_group.setLayout(door_layout)
        scroll_layout.addWidget(door_group)
        
        # PIR状态
        pir_group = QGroupBox("PIR运动检测模拟")
        pir_layout = QHBoxLayout()
        
        self.pir_static_radio = QRadioButton("静止")
        self.pir_motion_radio = QRadioButton("检测到运动")
        self.pir_static_radio.setChecked(True)
        self.pir_static_radio.toggled.connect(self.update_pir_state)
        self.pir_motion_radio.toggled.connect(self.update_pir_state)
        
        pir_layout.addWidget(self.pir_static_radio)
        pir_layout.addWidget(self.pir_motion_radio)
        pir_group.setLayout(pir_layout)
        scroll_layout.addWidget(pir_group)
        
        # YOLO检测模拟
        yolo_group = QGroupBox("YOLO检测模拟")
        yolo_layout = QVBoxLayout()
        
        # 检测目标选择
        detection_layout = QHBoxLayout()
        detection_label = QLabel("检测目标:")
        self.detection_combo = QComboBox()
        self.detection_combo.addItems(["无", "儿童", "成人"])
        self.detection_combo.currentIndexChanged.connect(self.update_yolo_detection)
        
        detection_layout.addWidget(detection_label)
        detection_layout.addWidget(self.detection_combo)
        detection_layout.addStretch()
        
        # 置信度
        conf_layout = QHBoxLayout()
        conf_label = QLabel("置信度:")
        self.conf_spinbox = QDoubleSpinBox()
        self.conf_spinbox.setRange(0.0, 1.0)
        self.conf_spinbox.setSingleStep(0.1)
        self.conf_spinbox.setValue(0.8)
        self.conf_spinbox.valueChanged.connect(self.update_confidence)
        
        conf_layout.addWidget(conf_label)
        conf_layout.addWidget(self.conf_spinbox)
        conf_layout.addStretch()
        
        yolo_layout.addLayout(detection_layout)
        yolo_layout.addLayout(conf_layout)
        yolo_group.setLayout(yolo_layout)
        scroll_layout.addWidget(yolo_group)
        
        # 控制按钮
        button_layout = QHBoxLayout()
        
        self.apply_btn = QPushButton("应用模拟数据")
        self.apply_btn.clicked.connect(self.apply_simulation)
        self.apply_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        
        self.reset_btn = QPushButton("重置为默认")
        self.reset_btn.clicked.connect(self.reset_simulation)
        
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.reset_btn)
        button_layout.addWidget(self.close_btn)
        
        scroll_layout.addLayout(button_layout)
        
        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        scroll_layout.addWidget(self.status_label)
    
    def update_temp_label(self):
        """更新温度标签"""
        value = self.temp_slider.value() / 10.0
        self.temp_label.setText(f"温度: {value:.1f}°C")
        self.simulated_data["temperature"] = value
    
    def update_humid_label(self):
        """更新湿度标签"""
        value = self.humid_slider.value() / 10.0
        self.humid_label.setText(f"湿度: {value:.1f}%")
        self.simulated_data["humidity"] = value
    
    def update_co2_label(self):
        """更新CO2标签"""
        value = self.co2_slider.value()
        self.co2_label.setText(f"CO2浓度: {value}ppm")
        self.simulated_data["eco2"] = value
    
    def update_tvoc_label(self):
        """更新TVOC标签"""
        value = self.tvoc_slider.value()
        self.tvoc_label.setText(f"TVOC浓度: {value}ppb")
        self.simulated_data["tvoc"] = value
    
    def update_object_temp_label(self):
        """更新人体温度标签"""
        value = self.object_temp_slider.value() / 10.0
        self.object_temp_label.setText(f"人体温度: {value:.1f}°C")
        self.simulated_data["object_temp"] = value
    
    def update_door_state(self):
        """更新车门状态"""
        if self.door_open_radio.isChecked():
            self.simulated_data["door_closed"] = False
        else:
            self.simulated_data["door_closed"] = True
    
    def update_pir_state(self):
        """更新PIR状态"""
        if self.pir_motion_radio.isChecked():
            self.simulated_data["pir_state"] = True
        else:
            self.simulated_data["pir_state"] = False
    
    def update_yolo_detection(self, index):
        """更新YOLO检测"""
        if index == 0:  # 无
            self.simulated_data["child_detected"] = False
            self.simulated_data["human_detected"] = False
            self.simulated_data["child_count"] = 0
            self.simulated_data["adult_count"] = 0
        elif index == 1:  # 儿童
            self.simulated_data["child_detected"] = True
            self.simulated_data["human_detected"] = True
            self.simulated_data["child_count"] = 1
            self.simulated_data["adult_count"] = 0
        elif index == 2:  # 成人
            self.simulated_data["child_detected"] = False
            self.simulated_data["human_detected"] = True
            self.simulated_data["child_count"] = 0
            self.simulated_data["adult_count"] = 1
    
    def update_confidence(self, value):
        """更新置信度"""
        self.simulated_data["confidence"] = value
    
    def apply_simulation(self):
        """应用模拟数据到主系统"""
        if self.parent:
            # 创建模拟的传感器数据
            simulated_sensor_data = SensorData(
                timestamp=time.time(),
                temperature=self.simulated_data["temperature"],
                humidity=self.simulated_data["humidity"],
                aqi=2,  # 默认良
                tvoc=self.simulated_data["tvoc"],
                eco2=self.simulated_data["eco2"],
                object_temp=self.simulated_data["object_temp"],
                human_detected=self.simulated_data["human_detected"],
                child_detected=self.simulated_data["child_detected"],
                child_confidence=self.simulated_data["confidence"],
                door_closed=self.simulated_data["door_closed"],
                pir_state=self.simulated_data["pir_state"]
            )
            
            # 创建模拟的检测结果
            if self.simulated_data["human_detected"]:
                simulated_detection_result = DetectionResult(
                    timestamp=time.time(),
                    child_detected=self.simulated_data["child_detected"],
                    confidence=self.simulated_data["confidence"],
                    bbox=[100, 100, 200, 200],
                    child_count=self.simulated_data["child_count"],
                    adult_count=self.simulated_data["adult_count"]
                )
            else:
                simulated_detection_result = None
            
            # 设置父窗口的模拟数据
            self.parent.set_simulated_data(simulated_sensor_data, simulated_detection_result)
            
            # 更新状态标签
            self.status_label.setText(f"✓ 模拟数据已应用 ({datetime.now().strftime('%H:%M:%S')})")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            
            # 触发风险评估并执行相应动作
            self.parent.assess_risk_and_execute()
    
    def reset_simulation(self):
        """重置为默认值"""
        self.temp_slider.setValue(250)  # 25.0°C
        self.humid_slider.setValue(500)  # 50.0%
        self.co2_slider.setValue(400)  # 400ppm
        self.tvoc_slider.setValue(50)  # 50ppb
        self.object_temp_slider.setValue(300)  # 30.0°C
        self.door_closed_radio.setChecked(True)
        self.pir_static_radio.setChecked(True)
        self.detection_combo.setCurrentIndex(0)  # 无
        self.conf_spinbox.setValue(0.8)
        
        self.status_label.setText("✓ 模拟数据已重置")
        self.status_label.setStyleSheet("color: blue; font-weight: bold;")

# ==================== 数据分析窗口（v1.0优化版：平滑 + 实时滚动） ====================
class DataAnalysisWindow(QMainWindow):
    """数据分析窗口 - 平滑实时曲线版"""
    
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager
        self.setWindowTitle("实时数据分析")
        self.setGeometry(200, 200, 1400, 800)
        
        # 存储历史数据用于绘图
        self.max_points = 300  # 显示最近300个点（约5分钟，如果1秒1点）
        self.timestamps = []
        self.temps = []
        self.humids = []
        self.tvocs = []
        self.eco2s = []
        
        self.init_ui()
        self.start_timer()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout()
        central.setLayout(layout)

        # 使用 pyqtgraph 实现高效实时曲线
        self.plot_temp_hum = PlotWidget(title="温湿度实时趋势")
        self.plot_temp_hum.setLabel('left', '温度 (°C) / 湿度 (%)')
        self.plot_temp_hum.setLabel('bottom', '时间')
        self.plot_temp_hum.showGrid(x=True, y=True, alpha=0.3)
        self.plot_temp_hum.setYRange(0, 100)
        self.plot_temp_hum.addLegend()

        self.curve_temp = self.plot_temp_hum.plot([], [], pen=mkPen('r', width=3), name="温度")
        self.curve_hum = self.plot_temp_hum.plot([], [], pen=mkPen('b', width=3), name="湿度")

        self.plot_air = PlotWidget(title="TVOC & eCO2 实时趋势")
        self.plot_air.setLabel('left', '浓度')
        self.plot_air.setLabel('bottom', '时间')
        self.plot_air.showGrid(x=True, y=True, alpha=0.3)
        self.plot_air.addLegend()

        self.curve_tvoc = self.plot_air.plot([], [], pen=mkPen('g', width=3), name="TVOC (ppb)")
        self.curve_eco2 = self.plot_air.plot([], [], pen=mkPen('m', width=3), name="eCO2 (ppm)")

        # 按钮
        btn_layout = QHBoxLayout()
        refresh_btn = QPushButton("手动刷新")
        refresh_btn.clicked.connect(self.refresh_charts)
        clear_btn = QPushButton("清空曲线")
        clear_btn.clicked.connect(self.clear_data)
        btn_layout.addWidget(refresh_btn)
        btn_layout.addWidget(clear_btn)
        btn_layout.addStretch()

        layout.addWidget(self.plot_temp_hum)
        layout.addWidget(self.plot_air)
        layout.addLayout(btn_layout)

    def start_timer(self):
        """每秒自动更新一次"""
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_charts)
        self.timer.start(1000)  # 1秒更新一次

    def refresh_charts(self):
        """刷新数据并绘制平滑曲线"""
        df = self.db_manager.get_recent_data(self.max_points + 50)  # 多取点用于平滑
        if df.empty:
            return

        # 提取数据
        times = pd.to_datetime(df['timestamp'], unit='s')
        temps = df['temperature'].astype(float).values
        humids = df['humidity'].astype(float).values
        tvocs = df['tvoc'].astype(int).values
        eco2s = df['eco2'].astype(int).values

        # 只保留最新 max_points 个点
        if len(times) > self.max_points:
            times = times[-self.max_points:]
            temps = temps[-self.max_points:]
            humids = humids[-self.max_points:]
            tvocs = tvocs[-self.max_points:]
            eco2s = eco2s[-self.max_points:]

        # 转为相对时间（秒）
        if len(times) > 0:
            t0 = times.iloc[0]
            x = [(t - t0).total_seconds() for t in times]
        else:
            x = []

        # 更新存储
        self.timestamps = x
        self.temps = temps.tolist()
        self.humids = humids.tolist()
        self.tvocs = tvocs.tolist()
        self.eco2s = eco2s.tolist()

        # 绘制平滑曲线
        self.update_smooth_plot(self.plot_temp_hum, [self.curve_temp, self.curve_hum],
                                x, [temps, humids], ['温度', '湿度'])
        self.update_smooth_plot(self.plot_air, [self.curve_tvoc, self.curve_eco2],
                                x, [tvocs, eco2s], ['TVOC', 'eCO2'])

    def update_smooth_plot(self, plot_widget, curves, x, y_lists, names):
        """绘制平滑曲线（三次样条插值）"""
        if len(x) < 4:
            # 数据太少，直接画折线
            for curve, y in zip(curves, y_lists):
                curve.setData(x, y)
            return

        try:
            # 生成更密集的X轴用于平滑
            x_smooth = np.linspace(min(x), max(x), len(x) * 10)

            for y, curve in zip(y_lists, curves):
                # 三次样条插值
                spl = make_interp_spline(x, y, k=3)
                y_smooth = spl(x_smooth)

                # 保留原数据点（可选）
                curve.setData(x, y, pen=None, symbol='o', symbolSize=4, symbolBrush=curve.opts['pen'].color())
                # 绘制平滑曲线
                curve_plot = plot_widget.plot(x_smooth, y_smooth,
                                              pen=curve.opts['pen'],
                                              name=names[y_lists.index(y)])

                # 替换曲线对象（pyqtgraph不支持直接替换，重新创建）
                if hasattr(curve, 'smooth_curve'):
                    plot_widget.removeItem(curve.smooth_curve)
                curve.smooth_curve = curve_plot

        except Exception as e:
            # print(f"平滑曲线绘制失败，使用原始折线: {e}")
            for curve, y in zip(curves, y_lists):
                curve.setData(x, y)

    def clear_data(self):
        """清空曲线"""
        self.timestamps = []
        self.temps = []
        self.humids = []
        self.tvocs = []
        self.eco2s = []
        self.curve_temp.setData([], [])
        self.curve_hum.setData([], [])
        self.curve_tvoc.setData([], [])
        self.curve_eco2.setData([], [])

# ==================== 设置窗口（v1.0优化版，删除邮件设置，支持持久化） ====================
class SettingsWindow(QDialog):
    """设置窗口 - 支持配置持久化"""
    
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.parent = parent  # 保存父窗口引用
        self.init_ui()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("系统设置")
        self.setGeometry(300, 300, 500, 400)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # 创建选项卡
        tab_widget = QTabWidget()
        
        # MQTT设置
        mqtt_tab = QWidget()
        mqtt_layout = QFormLayout()
        
        self.mqtt_broker_edit = QLineEdit(self.config.MQTT_BROKER)
        self.mqtt_port_edit = QLineEdit(str(self.config.MQTT_PORT))
        self.mqtt_user_edit = QLineEdit(self.config.MQTT_USER)
        self.mqtt_password_edit = QLineEdit(self.config.MQTT_PASSWORD)
        self.mqtt_password_edit.setEchoMode(QLineEdit.Password)
        
        mqtt_layout.addRow("MQTT Broker:", self.mqtt_broker_edit)
        mqtt_layout.addRow("MQTT 端口:", self.mqtt_port_edit)
        mqtt_layout.addRow("MQTT 用户名:", self.mqtt_user_edit)
        mqtt_layout.addRow("MQTT 密码:", self.mqtt_password_edit)
        
        mqtt_tab.setLayout(mqtt_layout)
        tab_widget.addTab(mqtt_tab, "MQTT设置")
        
        # 报警阈值设置
        threshold_tab = QWidget()
        threshold_layout = QFormLayout()
        
        self.temp_high_edit = QLineEdit(str(self.config.THRESHOLDS["temperature_high"]))
        self.temp_extreme_edit = QLineEdit(str(self.config.THRESHOLDS["temperature_extreme"]))
        self.humidity_edit = QLineEdit(str(self.config.THRESHOLDS["humidity_high"]))
        self.co2_high_edit = QLineEdit(str(self.config.THRESHOLDS["co2_high"]))
        self.co2_extreme_edit = QLineEdit(str(self.config.THRESHOLDS["co2_extreme"]))
        self.tvoc_high_edit = QLineEdit(str(self.config.THRESHOLDS["tvoc_high"]))
        self.tvoc_extreme_edit = QLineEdit(str(self.config.THRESHOLDS["tvoc_extreme"]))
        
        threshold_layout.addRow("高温阈值 (°C):", self.temp_high_edit)
        threshold_layout.addRow("极端高温阈值 (°C):", self.temp_extreme_edit)
        threshold_layout.addRow("高湿度阈值 (%):", self.humidity_edit)
        threshold_layout.addRow("高CO2阈值 (ppm):", self.co2_high_edit)
        threshold_layout.addRow("极端CO2阈值 (ppm):", self.co2_extreme_edit)
        threshold_layout.addRow("高TVOC阈值 (ppb):", self.tvoc_high_edit)
        threshold_layout.addRow("极端TVOC阈值 (ppb):", self.tvoc_extreme_edit)
        
        threshold_tab.setLayout(threshold_layout)
        tab_widget.addTab(threshold_tab, "报警阈值")
        
        layout.addWidget(tab_widget)
        
        # 按钮
        button_layout = QHBoxLayout()
        
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.save_settings)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.close)
        
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def save_settings(self):
        """保存设置 - 立即生效"""
        try:
            # 更新config对象
            self.config.MQTT_BROKER = self.mqtt_broker_edit.text().strip()
            self.config.MQTT_PORT = int(self.mqtt_port_edit.text())
            self.config.MQTT_USER = self.mqtt_user_edit.text().strip()
            self.config.MQTT_PASSWORD = self.mqtt_password_edit.text()
            
            # 更新阈值
            self.config.THRESHOLDS["temperature_high"] = float(self.temp_high_edit.text())
            self.config.THRESHOLDS["temperature_extreme"] = float(self.temp_extreme_edit.text())
            self.config.THRESHOLDS["humidity_high"] = float(self.humidity_edit.text())
            self.config.THRESHOLDS["co2_high"] = int(self.co2_high_edit.text())
            self.config.THRESHOLDS["co2_extreme"] = int(self.co2_extreme_edit.text())
            self.config.THRESHOLDS["tvoc_high"] = int(self.tvoc_high_edit.text())
            self.config.THRESHOLDS["tvoc_extreme"] = int(self.tvoc_extreme_edit.text())
            
            # 保存到文件
            self.config.save_config()
            
            # 立即更新风险引擎的阈值（关键！）
            if self.parent and hasattr(self.parent, 'risk_engine'):
                self.parent.risk_engine.config = self.config  # 更新引用
            
            QMessageBox.information(self, "成功", "设置已保存并立即生效！")
            self.close()
            
        except ValueError as e:
            QMessageBox.warning(self, "错误", f"输入格式错误: {e}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败: {str(e)}")

# ==================== 主窗口（整合版，带界面美化和短信报警） ====================
class MainWindow(QMainWindow):
    """主GUI窗口"""
    
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        
        # 应用界面美化样式表（从v0.4.1.txt中提取）
        self.setStyleSheet("""
            QWidget {
                background-color: #f0f4f8;
                font-family: Arial;
                font-size: 14px;
                color: #333;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: 8px;
                background-color: #ffffff;
                padding: 10px;
            }
            QLabel {
                color: #555;
                padding: 5px;
            }
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4CAF50, stop:1 #45a049);
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #66bb6a, stop:1 #5cb85c);
            }
            QListWidget {
                background-color: #ffffff;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
            QFrame {
                background-color: #ffffff;
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 10px;
            }
            /* 摄像头标签样式 */
            QLabel#camera_label {
                border: 2px solid #ccc;
                border-radius: 8px;
                background-color: #e8e8e8;
            }
            /* 风险指示器动态颜色（已在代码中设置） */
        """)
        
        self.setWindowTitle("车载儿童安全监控系统 - 集成短信报警和报警同步")
        self.setGeometry(100, 100, 1600, 900)
        
        # 初始化组件
        self.db_manager = DatabaseManager(config.DB_PATH)
        self.comm_manager = CommunicationManager(config)
        self.vision_manager = VisionManager(config)
        # 设置通信管理器引用，以便VisionManager可以发送MQTT消息
        self.vision_manager.comm_manager = self.comm_manager
        self.notif_manager = NotificationManager(config, self.db_manager, self.comm_manager)
        self.risk_engine = RiskEngine(config)
        
        self.sensor_data = None
        self.detection_result = None
        self.aqi_rating = {1: "优", 2: "良", 3: "中", 4: "差", 5: "极差"}
        
        # 新增：模拟数据相关变量
        self.use_simulated_data = False
        self.simulated_sensor_data = None
        self.simulated_detection_result = None
        
        # 创建UI
        self.create_ui()
        
        # 启动定时器
        self.start_timers()
        
        # 启动视频流
        self.vision_manager.start_stream()
        
        # 窗口关闭事件处理
        self.closeEvent = self.on_close
    
    def on_close(self, event):
        """窗口关闭事件处理"""
        # 停止所有组件
        self.vision_manager.stop_stream()
        self.comm_manager.stop()
        event.accept()
    
    def create_ui(self):
        """创建用户界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout()
        central_widget.setLayout(main_layout)
        
        # 左侧面板（视频和检测）
        left_panel = QFrame()
        left_panel.setFrameStyle(QFrame.StyledPanel)
        left_layout = QVBoxLayout()
        left_panel.setLayout(left_layout)
        
        # 视频显示
        video_group = QGroupBox("实时视频监控")
        video_layout = QVBoxLayout()
        self.camera_label = QLabel()
        self.camera_label.setObjectName("camera_label")  # 设置对象名用于样式表选择器
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setMinimumSize(640, 480)
        video_layout.addWidget(self.camera_label)
        video_group.setLayout(video_layout)
        left_layout.addWidget(video_group)
        
        # 检测信息
        detect_group = QGroupBox("检测信息")
        detect_layout = QGridLayout()
        
        self.child_count_label = QLabel("儿童数量: 0")
        self.adult_count_label = QLabel("成人数量: 0")
        self.confidence_label = QLabel("置信度: 0.00")
        self.detect_status = QLabel("状态: 未检测")
        # 新增：人员置信度显示
        self.person_confidence_label = QLabel("人员置信度: 0%")
        
        detect_layout.addWidget(self.child_count_label, 0, 0)
        detect_layout.addWidget(self.adult_count_label, 0, 1)
        detect_layout.addWidget(self.confidence_label, 1, 0)
        detect_layout.addWidget(self.detect_status, 1, 1)
        detect_layout.addWidget(self.person_confidence_label, 2, 0, 1, 2)
        
        detect_group.setLayout(detect_layout)
        left_layout.addWidget(detect_group)
        
        # 中间面板（传感器数据）
        center_panel = QFrame()
        center_panel.setFrameStyle(QFrame.StyledPanel)
        center_layout = QVBoxLayout()
        center_panel.setLayout(center_layout)
        
        # 环境监测组
        sensor_group = QGroupBox("环境监测")
        sensor_layout = QGridLayout()
        
        self.temp_label = QLabel("温度: -- °C")
        self.humidity_label = QLabel("湿度: -- %")
        self.co2_label = QLabel("CO2: -- ppm")
        self.tvoc_label = QLabel("TVOC: -- ppb")
        self.aqi_label = QLabel("AQI: --")
        self.object_temp_label = QLabel("人体温度: -- °C")
        
        sensor_layout.addWidget(self.temp_label, 0, 0)
        sensor_layout.addWidget(self.humidity_label, 0, 1)
        sensor_layout.addWidget(self.co2_label, 1, 0)
        sensor_layout.addWidget(self.tvoc_label, 1, 1)
        sensor_layout.addWidget(self.aqi_label, 2, 0)
        sensor_layout.addWidget(self.object_temp_label, 2, 1)
        
        sensor_group.setLayout(sensor_layout)
        center_layout.addWidget(sensor_group)
        
        # 状态监测组
        status_group = QGroupBox("状态监测")
        status_layout = QGridLayout()
        
        self.door_label = QLabel("车门: --")
        self.pir_label = QLabel("运动检测: --")
        
        status_layout.addWidget(self.door_label, 0, 0)
        status_layout.addWidget(self.pir_label, 0, 1)
        
        status_group.setLayout(status_layout)
        center_layout.addWidget(status_group)
        
        # 右侧面板（控制和报警）
        right_panel = QFrame()
        right_panel.setFrameStyle(QFrame.StyledPanel)
        right_layout = QVBoxLayout()
        right_panel.setLayout(right_layout)
        
        # 风险显示
        risk_group = QGroupBox("风险评估")
        risk_layout = QVBoxLayout()
        
        self.risk_indicator = QLabel("正常")
        self.risk_indicator.setAlignment(Qt.AlignCenter)
        self.risk_indicator.setStyleSheet("font-size: 24px; font-weight: bold; color: green;")
        
        self.risk_detail = QLabel("系统运行正常")
        self.risk_detail.setWordWrap(True)
        
        risk_layout.addWidget(self.risk_indicator)
        risk_layout.addWidget(self.risk_detail)
        risk_group.setLayout(risk_layout)
        right_layout.addWidget(risk_group)
        
        # 控制面板（整合v0.4和v1.0功能）
        control_group = QGroupBox("控制面板")
        control_layout = QVBoxLayout()
        
        self.lower_window_btn = QPushButton("一键降窗")
        self.lower_window_btn.clicked.connect(self.lower_windows)
        
        self.test_alarm_btn = QPushButton("测试报警")
        self.test_alarm_btn.clicked.connect(self.test_alarm)
        
        self.test_sms_btn = QPushButton("测试短信")  # 新增：测试短信按钮
        self.test_sms_btn.clicked.connect(self.test_sms)
        
        self.reset_btn = QPushButton("系统复位")
        self.reset_btn.clicked.connect(self.reset_system)
        
        # ======== 新增：自动抓拍开关 ========
        self.auto_capture_btn = QPushButton()
        self.update_auto_capture_button()  # 初始更新按钮文字和颜色
        
        self.auto_capture_btn.clicked.connect(self.toggle_auto_capture)
        control_layout.addWidget(self.auto_capture_btn)
        # =====================================
        
        self.screenshot_btn = QPushButton("保存截图")
        self.screenshot_btn.clicked.connect(self.save_screenshot)
        
        control_layout.addWidget(self.lower_window_btn)
        control_layout.addWidget(self.test_alarm_btn)
        control_layout.addWidget(self.test_sms_btn)
        control_layout.addWidget(self.reset_btn)
        control_layout.addWidget(self.screenshot_btn)
        
        control_group.setLayout(control_layout)
        right_layout.addWidget(control_group)
        
        # 报警历史
        alert_group = QGroupBox("报警历史")
        alert_layout = QVBoxLayout()
        
        self.alert_list = QListWidget()
        alert_layout.addWidget(self.alert_list)
        
        alert_group.setLayout(alert_layout)
        right_layout.addWidget(alert_group)
        
        # 添加到主布局
        main_layout.addWidget(left_panel, 4)
        main_layout.addWidget(center_panel, 3)
        main_layout.addWidget(right_panel, 3)
        
        # 创建菜单栏
        self.create_menu_bar()
    
    def update_auto_capture_button(self):
        """根据当前开关状态更新按钮文字和颜色"""
        if self.config.AUTO_CAPTURE_ENABLED:
            self.auto_capture_btn.setText("自动抓拍：已开启")
            self.auto_capture_btn.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold;"
            )
        else:
            self.auto_capture_btn.setText("自动抓拍：已关闭")
            self.auto_capture_btn.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold;"
            )

    def toggle_auto_capture(self):
        """切换自动抓拍开关状态"""
        self.config.AUTO_CAPTURE_ENABLED = not self.config.AUTO_CAPTURE_ENABLED
        self.update_auto_capture_button()
        
        # 保存配置到文件
        self.config.save_config()
        
        # 可选：提示用户
        status = "开启" if self.config.AUTO_CAPTURE_ENABLED else "关闭"
        QMessageBox.information(self, "自动抓拍", f"自动抓拍功能已{status}")
        
        # 如果关闭了自动抓拍，可顺便重置抓拍相关标志（防止残留）
        if not self.config.AUTO_CAPTURE_ENABLED:
            self.vision_manager.person_detected_flag = False
            self.vision_manager.last_capture_time = 0
    
    def create_menu_bar(self):
        """创建菜单栏"""
        menubar = self.menuBar()
        
        # 文件菜单
        file_menu = menubar.addMenu('文件')
        
        export_action = QAction('导出数据', self)
        export_action.triggered.connect(self.export_data)
        file_menu.addAction(export_action)
        
        exit_action = QAction('退出', self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 视图菜单
        view_menu = menubar.addMenu('视图')
        
        data_view_action = QAction('数据分析', self)
        data_view_action.triggered.connect(self.show_data_analysis)
        view_menu.addAction(data_view_action)
        
        settings_action = QAction('系统设置', self)
        settings_action.triggered.connect(self.show_settings)
        view_menu.addAction(settings_action)
        
        # 新增：调试菜单
        debug_menu = menubar.addMenu('调试')
        
        simulation_action = QAction('模拟测试', self)
        simulation_action.triggered.connect(self.show_simulation)
        debug_menu.addAction(simulation_action)
        
        # 新增：启用/禁用模拟数据
        self.use_simulated_action = QAction('使用模拟数据', self, checkable=True)
        self.use_simulated_action.toggled.connect(self.toggle_simulated_data)
        debug_menu.addAction(self.use_simulated_action)
        
        reset_simulated_action = QAction('清除模拟数据', self)
        reset_simulated_action.triggered.connect(self.clear_simulated_data)
        debug_menu.addAction(reset_simulated_action)
        
        # 新增：查看误判日志
        false_positive_action = QAction('查看误判日志', self)
        false_positive_action.triggered.connect(self.show_false_positive_logs)
        debug_menu.addAction(false_positive_action)
    
    def show_false_positive_logs(self):
        """显示误判日志"""
        logs = self.risk_engine.get_detection_summary()
        
        # 创建日志对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("误判日志")
        dialog.setGeometry(400, 300, 600, 400)
        
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        
        # 创建文本编辑框显示日志
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("Courier", 10))
        
        # 格式化日志信息
        log_text = f"人员置信度: {logs['person_confidence']}%\n"
        log_text += f"连续检测帧数: {logs['consecutive_detections']}\n"
        log_text += f"误判事件总数: {logs['false_positive_count']}\n\n"
        log_text += "最近5个误判事件:\n"
        
        if logs['recent_false_positives']:
            for i, event in enumerate(logs['recent_false_positives'], 1):
                time_str = datetime.fromtimestamp(event['timestamp']).strftime("%H:%M:%S")
                log_text += f"{i}. [{time_str}] {event['reason']} (置信度: {event['person_confidence']}%)\n"
        else:
            log_text += "无近期误判事件\n"
        
        text_edit.setText(log_text)
        layout.addWidget(text_edit)
        
        # 添加关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)
        
        dialog.exec_()
    
    def show_simulation(self):
        """显示模拟测试窗口"""
        self.simulation_window = SimulationWindow(self)
        self.simulation_window.show()
    
    def toggle_simulated_data(self, enabled):
        """切换是否使用模拟数据"""
        self.use_simulated_data = enabled
        
        if enabled:
            QMessageBox.information(self, "模拟模式", "已启用模拟数据模式。系统将使用模拟数据替代真实传感器数据。")
        else:
            QMessageBox.information(self, "模拟模式", "已禁用模拟数据模式。系统将使用真实传感器数据。")
    
    def set_simulated_data(self, sensor_data: SensorData, detection_result: DetectionResult = None):
        """设置模拟数据"""
        self.simulated_sensor_data = sensor_data
        self.simulated_detection_result = detection_result
        self.use_simulated_data = True
        self.use_simulated_action.setChecked(True)
    
    def clear_simulated_data(self):
        """清除模拟数据"""
        self.simulated_sensor_data = None
        self.simulated_detection_result = None
        self.use_simulated_data = False
        self.use_simulated_action.setChecked(False)
        
        QMessageBox.information(self, "清除模拟", "已清除模拟数据，恢复使用真实传感器数据。")
    
    def start_timers(self):
        """启动定时器"""
        # 更新摄像头显示
        self.camera_timer = QTimer()
        self.camera_timer.timeout.connect(self.update_camera_display)
        self.camera_timer.start(50)  # 20fps
        
        # 更新传感器数据
        self.sensor_timer = QTimer()
        self.sensor_timer.timeout.connect(self.update_sensor_display)
        self.sensor_timer.start(2000)  # 改为2秒一次，匹配发送频率
        
        # 处理通信数据
        self.comm_timer = QTimer()
        self.comm_timer.timeout.connect(self.process_comm_data)
        self.comm_timer.start(100)
        
        # 风险评估和执行动作
        self.risk_timer = QTimer()
        self.risk_timer.timeout.connect(self.assess_risk_and_execute)
        self.risk_timer.start(2000)  # 改为2秒一次，匹配传感器更新频率
    
    def update_camera_display(self):
        """更新摄像头显示"""
        frame = self.vision_manager.get_frame_with_detections()
        if frame is not None:
            # 转换为Qt图像格式
            height, width, channel = frame.shape
            bytes_per_line = 3 * width
            qt_image = QImage(frame.data, width, height, bytes_per_line, QImage.Format_RGB888)
            qt_image = qt_image.rgbSwapped()
            
            # 缩放以适应标签
            pixmap = QPixmap.fromImage(qt_image)
            scaled_pixmap = pixmap.scaled(
                self.camera_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.camera_label.setPixmap(scaled_pixmap)
        else:
            # 显示等待视频流
            self.camera_label.setText("等待视频流...")
            self.camera_label.setPixmap(QPixmap())
    
    def update_sensor_display(self):
        """更新传感器数据显示"""
        # 使用模拟数据或真实数据
        if self.use_simulated_data and self.simulated_sensor_data:
            sensor_data = self.simulated_sensor_data
            detection_result = self.simulated_detection_result
            data_source = "(模拟)"
        elif self.sensor_data:
            sensor_data = self.sensor_data
            detection_result = self.detection_result
            data_source = ""
        else:
            return
        
        # 更新传感器数据显示
        self.temp_label.setText(f"温度: {sensor_data.temperature:.1f} °C {data_source}")
        self.humidity_label.setText(f"湿度: {sensor_data.humidity:.1f} % {data_source}")
        self.co2_label.setText(f"CO2: {sensor_data.eco2} ppm {data_source}")
        self.tvoc_label.setText(f"TVOC: {sensor_data.tvoc} ppb {data_source}")
        aqi_value = sensor_data.aqi
        aqi_desc = self.aqi_rating.get(aqi_value, "未知")
        self.aqi_label.setText(f"AQI: {aqi_value} ({aqi_desc}) {data_source}")
        self.door_label.setText(f"车门: {'关闭' if sensor_data.door_closed else '打开'} {data_source}")
        self.pir_label.setText(f"运动检测: {'是' if sensor_data.pir_state else '否'} {data_source}")
        self.object_temp_label.setText(f"人体温度: {sensor_data.object_temp:.1f} °C {data_source}")
        
        # 更新检测信息
        if detection_result:
            self.child_count_label.setText(f"儿童数量: {detection_result.child_count} {data_source}")
            self.adult_count_label.setText(f"成人数量: {detection_result.adult_count} {data_source}")
            self.confidence_label.setText(f"置信度: {detection_result.confidence:.2f} {data_source}")
            self.detect_status.setText(f"状态: {'检测到目标' if (detection_result.child_count + detection_result.adult_count) > 0 else '未检测到目标'} {data_source}")
        
        # 新增：更新人员置信度显示（使用不同颜色）
        confidence = self.risk_engine.person_confidence
        if confidence < 30:
            confidence_color = "green"
        elif confidence < 60:
            confidence_color = "orange"
        else:
            confidence_color = "red"
        self.person_confidence_label.setText(f"人员置信度: <span style='color:{confidence_color}'>{confidence}%</span> {data_source}")
        
        # 新增：每2秒发送一次传感器数据（如果使用真实数据）
        if not self.use_simulated_data:
            self.comm_manager.send_sensor_data(sensor_data)
    
    def process_comm_data(self):
        """处理通信数据"""
        while True:
            try:
                data = self.comm_manager.data_queue.get_nowait()
            except queue.Empty:
                break
            
            # 如果正在使用模拟数据，跳过真实数据处理
            if self.use_simulated_data:
                continue
            
            # 支持串口发送的传感器数据（没有topic字段）
            if (
                "temperature" in data and 
                "humidity" in data and 
                "eco2" in data and 
                "object_temp" in data
            ) or data.get("topic") == self.config.MQTT_TOPICS["status"]:
                self.process_sensor_data(data)
            
            # 处理检测结果
            elif data.get("topic") == self.config.MQTT_TOPICS["child_detection"]:
                self.process_detection_data(data)
    
    def process_sensor_data(self, data: dict):
        """处理传感器数据"""
        try:
            sensor_data = SensorData(
                timestamp=data.get("timestamp", time.time()),
                temperature=data.get("temperature", 0.0),
                humidity=data.get("humidity", 0.0),
                aqi=data.get("aqi", 0),
                tvoc=data.get("tvoc", 0),
                eco2=data.get("eco2", 0),
                object_temp=data.get("object_temp", 0.0),
                human_detected=data.get("human_detected", False),
                child_detected=data.get("child_detected", False),
                child_confidence=data.get("child_confidence", 0.0),
                door_closed=data.get("door_closed", False),
                pir_state=data.get("pir_state", False)
            )
            self.sensor_data = sensor_data
            self.db_manager.save_sensor_data(sensor_data)
            
        except Exception as e:
            print(f"处理传感器数据错误: {e}")
    
    def process_detection_data(self, data: dict):
        """处理检测数据"""
        try:
            detection_result = DetectionResult(
                timestamp=data.get("timestamp", time.time()),
                child_detected=data.get("child_detected", False),
                confidence=data.get("confidence", 0.0),
                bbox=data.get("bbox", []),
                child_count=data.get("child_count", 0),
                adult_count=data.get("adult_count", 0)
            )
            self.detection_result = detection_result
            self.db_manager.save_detection_result(detection_result)
            
        except Exception as e:
            print(f"处理检测数据错误: {e}")
    
    def assess_risk_and_execute(self):
        """评估风险并执行相应动作（仅状态变化时发送MQTT）"""
        # 初始化状态缓存（存储关键字段，判断是否变化）
        if not hasattr(self, 'last_risk_state'):
            self.last_risk_state = {
                "risk_level": "",       # 风险等级（normal/warning/emergency）
                "reason_key": "",       # 核心原因标识（前2个原因拼接）
                "child_count": -1,      # 儿童数量
                "adult_count": -1,      # 成人数量
                "temperature": -999.0   # 核心环境参数（温度，用于判断环境变化）
            }
        

        # 选择使用模拟数据还是真实数据
        if self.use_simulated_data and self.simulated_sensor_data:
            sensor_data = self.simulated_sensor_data
            detection_result = self.simulated_detection_result
            data_source = "[模拟] "
        elif self.sensor_data:
            sensor_data = self.sensor_data
            detection_result = self.detection_result
            data_source = ""
        else:
            return
        
        # 合并视觉检测结果到传感器数据
        if detection_result:
            sensor_data.human_detected = (detection_result.child_count > 0 or detection_result.adult_count > 0)
            sensor_data.child_detected = (detection_result.child_count > 0)
            sensor_data.adult_count = detection_result.adult_count
            sensor_data.child_count = detection_result.child_count
            sensor_data.child_confidence = detection_result.confidence

        # 获取风险评估结果
        risk_level, reasons, need_alarm, need_auto_window, need_sms = self.risk_engine.assess_risk(
            sensor_data, detection_result
        )

        # 构建当前状态关键信息（用于与上一次状态对比）
        current_child_count = detection_result.child_count if detection_result else 0
        current_adult_count = detection_result.adult_count if detection_result else 0
        current_temp = sensor_data.temperature
        current_reason_key = "_".join(reasons[:2])  # 取前2个核心原因，避免无关细节变化误触发

        # 对比当前状态与上一次状态，判断是否发生变化
        state_changed = False
        if (self.last_risk_state["risk_level"] != risk_level or
            self.last_risk_state["reason_key"] != current_reason_key or
            self.last_risk_state["child_count"] != current_child_count or
            self.last_risk_state["adult_count"] != current_adult_count or
            abs(self.last_risk_state["temperature"] - current_temp) > 0.5):  # 温度变化超0.5℃才算变化
            state_changed = True

        # 仅当状态变化时，才发布风险状态到MQTT（核心优化）
        if state_changed:
            try:
                risk_status = {
                    "timestamp": time.time(),
                    "device_id": self.config.DEVICE_ID,
                    "risk_level": risk_level,
                    "reasons": reasons,
                    "description": self.risk_engine.get_risk_description(risk_level, reasons),
                    "is_simulated": bool(self.use_simulated_data),
                    "child_count": current_child_count,
                    "adult_count": current_adult_count,
                    "temperature": current_temp,
                }
                payload = json.dumps(risk_status, ensure_ascii=False)
                self.comm_manager.publish(self.config.MQTT_TOPICS.get("alerts", ""), payload, qos=1)
            except Exception as e:
                print(f"发布风险状态失败: {e}")
            
            # 更新状态缓存为当前状态，用于下一次对比
            self.last_risk_state = {
                "risk_level": risk_level,
                "reason_key": current_reason_key,
                "child_count": current_child_count,
                "adult_count": current_adult_count,
                "temperature": current_temp
            }

        # 发送传感器数据（按原有频率，不受状态变化影响）
        if not self.use_simulated_data:
            self.comm_manager.send_sensor_data(sensor_data)

        # 更新风险指示器显示
        if risk_level == "emergency":
            self.risk_indicator.setText("紧急")
            self.risk_indicator.setStyleSheet("font-size: 24px; font-weight: bold; color: red;")
            risk_description = self.risk_engine.get_risk_description(risk_level, reasons)
            self.risk_detail.setText(data_source + risk_description)
            
            # 执行紧急动作：自动降窗
            if need_auto_window and self.risk_engine.should_auto_window(time.time()):
                self.auto_lower_windows(risk_description)
            
            # 发送报警命令
            if need_alarm:
                self.send_alarm_command()
            
            # 创建紧急警报（自动触发短信）
            if need_sms:
                self.notif_manager.create_alert("emergency", data_source + risk_description)
            else:
                self.notif_manager.create_alert("emergency", data_source + risk_description)
            
        elif risk_level == "warning":
            self.risk_indicator.setText("警告")
            self.risk_indicator.setStyleSheet("font-size: 24px; font-weight: bold; color: orange;")
            risk_description = self.risk_engine.get_risk_description(risk_level, reasons)
            self.risk_detail.setText(data_source + risk_description)
            
            # 发送报警命令
            if need_alarm:
                self.send_alarm_command()
            
            # 创建警告警报（不发送短信）
            self.notif_manager.create_alert("warning", data_source + risk_description)
        else:
            self.risk_indicator.setText("正常")
            self.risk_indicator.setStyleSheet("font-size: 24px; font-weight: bold; color: green;")
            risk_description = self.risk_engine.get_risk_description(risk_level)
            self.risk_detail.setText(data_source + risk_description)
        
        # 更新本地报警历史列表
        self.update_alert_history()
    
    def send_alarm_command(self):
        """发送报警命令（与控制面板的测试报警一致）"""
        success = self.comm_manager.send_control_command("test_alarm")
        if success:
            print("报警命令已发送")
        else:
            print("报警命令发送失败")
    
    def auto_lower_windows(self, reason: str):
        """自动降窗"""
        current_time = time.time()
        if not self.risk_engine.should_auto_window(current_time):
            return  # 冷却中直接返回，不打印

        print(f"🚨 执行自动降窗: {reason}")  # 关键动作：只在实际执行时打印一次
        
        success = self.comm_manager.send_control_command("lower_window", {"percent": 100})
        if success:
            print("↓ 自动降窗命令已发送")
            auto_window_alert = AlertInfo(
                level="info",
                message=f"已执行自动降窗: {reason}",
                timestamp=current_time
            )
            self.db_manager.save_alert(auto_window_alert)
            self.update_alert_history()
    
    def update_alert_history(self):
        """更新报警历史列表 - 优化：只在有新报警时更新，避免闪烁"""
        recent_alerts = self.notif_manager.get_recent_alerts(15)
        
        # 只在有新报警或列表为空时更新
        if len(self.alert_list) == len(recent_alerts):
            # 检查最后一条是否相同
            if self.alert_list.count() > 0:
                last_item_text = self.alert_list.item(self.alert_list.count()-1).text()
                latest_alert = recent_alerts[-1]
                time_str = datetime.fromtimestamp(latest_alert.timestamp).strftime("%H:%M:%S")
                new_text = f"[{time_str}] {latest_alert.level}: {latest_alert.message}"
                if "[模拟]" in latest_alert.message:
                    new_text = f"[模拟] {new_text}"
                if last_item_text == new_text or "[模拟]" in last_item_text and "[模拟]" in new_text:
                    return  # 最新一条相同，不刷新
        
        # 有变化才更新
        self.alert_list.clear()
        displayed_messages = set()  # 临时防重复
        
        for alert in reversed(recent_alerts):
            time_str = datetime.fromtimestamp(alert.timestamp).strftime("%H:%M:%S")
            item_text = f"[{time_str}] {alert.level}: {alert.message}"
            if "[模拟]" in alert.message:
                item_text = f"[模拟] {item_text}"
            
            # 短时间内相同消息只显示一次（简单哈希）
            msg_key = f"{alert.level}:{alert.message[:30]}"
            if msg_key in displayed_messages:
                continue
            displayed_messages.add(msg_key)
            
            item = QListWidgetItem(item_text)
            if alert.level == "emergency":
                item.setForeground(QColor("red"))
            elif alert.level == "warning":
                item.setForeground(QColor("orange"))
            elif alert.level == "info":
                item.setForeground(QColor("blue"))
                
            if "[模拟]" in alert.message:
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
                
            self.alert_list.addItem(item)
    
    def lower_windows(self):
        """发送降窗命令（v0.4功能）"""
        success = self.comm_manager.send_control_command("lower_window", {"percent": 100})
        if success:
            QMessageBox.information(self, "命令发送", "一键降窗命令已发送")
        else:
            QMessageBox.warning(self, "发送失败", "无法发送命令，请检查连接")
    
    def test_alarm(self):
        """测试报警（v0.4功能：发送命令到ESP32）"""
        success = self.comm_manager.send_control_command("test_alarm")
        if success:
            alert_message = "这是一个测试报警"
            if self.use_simulated_data:
                alert_message = f"[模拟] {alert_message}"
            self.notif_manager.create_alert("warning", alert_message)
            self.update_alert_history()
            QMessageBox.information(self, "测试报警", "测试报警已触发")
        else:
            QMessageBox.warning(self, "发送失败", "无法发送测试报警命令，请检查连接")
    
    def test_sms(self):
        """测试短信发送"""
        test_message = "【测试短信】车载儿童安全监控系统短信功能测试正常"
        if self.use_simulated_data:
            test_message = f"[模拟] {test_message}"
        success = self.comm_manager.send_sms_command(test_message)
        if success:
            self.notif_manager.create_alert("info", f"测试短信已发送: {test_message}")
            self.update_alert_history()
            QMessageBox.information(self, "测试短信", "短信测试指令已发送")
        else:
            QMessageBox.warning(self, "发送失败", "短信测试指令发送失败")
    
    def reset_system(self):
        """增强系统复位功能：停止所有运行状态并重置冷却时间"""
        try:
            # 1. 发送系统复位命令到设备
            success = self.comm_manager.send_control_command("reset_system")
            
            # 2. 重置风险评估引擎（包括自动降窗冷却时间）
            self.risk_engine.reset()
            
            # 3. 重置通知管理器（包括短信冷却时间）
            self.notif_manager.reset()
            
            # 4. 重置视觉管理器状态
            self.vision_manager.reset()
            
            # 5. 重置模拟数据（如果正在使用）
            if self.use_simulated_data:
                self.use_simulated_data = False
                self.use_simulated_action.setChecked(False)
                self.simulated_sensor_data = None
                self.simulated_detection_result = None
                print("模拟数据已清除")
            
            # 6. 恢复自动抓拍默认开启（从配置文件重新加载）
            self.config.load_config()
            self.update_auto_capture_button()
            
            # 7. 更新UI显示
            self.risk_indicator.setText("正常")
            self.risk_indicator.setStyleSheet("font-size: 24px; font-weight: bold; color: green;")
            self.risk_detail.setText("系统已复位，所有冷却时间已重置")
            
            # 8. 添加复位记录到报警历史
            reset_alert = AlertInfo(
                level="info",
                message="系统已完全复位，所有冷却时间已重置",
                timestamp=time.time()
            )
            self.notif_manager.alerts.append(reset_alert)
            self.db_manager.save_alert(reset_alert)
            self.update_alert_history()
            
            # 9. 清空传感器数据队列
            while not self.comm_manager.data_queue.empty():
                try:
                    self.comm_manager.data_queue.get_nowait()
                except:
                    pass
            
            if success:
                QMessageBox.information(self, "系统复位", "系统已完全复位，所有冷却时间已重置，停止所有报警状态")
            else:
                QMessageBox.warning(self, "复位命令发送失败", "本地系统状态已重置，但设备复位命令发送失败，请检查连接")
                
        except Exception as e:
            QMessageBox.critical(self, "复位错误", f"系统复位过程中出现错误: {str(e)}")
    
    def save_screenshot(self):
        """保存当前截图"""
        frame = self.vision_manager.get_frame_with_detections()
        if frame is not None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.jpg"
            cv2.imwrite(filename, frame)
            QMessageBox.information(self, "保存成功", f"截图已保存为: {filename}")
        else:
            QMessageBox.warning(self, "保存失败", "没有可保存的视频帧")
    
    def export_data(self):
        """导出数据到CSV"""
        options = QFileDialog.Options()
        filename, _ = QFileDialog.getSaveFileName(self, "导出数据", "", "CSV文件 (*.csv);;所有文件 (*)", options=options)
        if filename:
            try:
                self.db_manager.export_to_csv("sensor_data", filename)
                QMessageBox.information(self, "导出成功", f"数据已导出到: {filename}")
            except Exception as e:
                QMessageBox.warning(self, "导出失败", f"导出数据时出错: {str(e)}")
    
    def show_data_analysis(self):
        """显示数据分析窗口（v1.0优化版）"""
        self.analysis_window = DataAnalysisWindow(self.db_manager)
        self.analysis_window.show()
    
    def show_settings(self):
        """显示系统设置窗口（v1.0优化版）"""
        self.settings_window = SettingsWindow(self.config, self)
        self.settings_window.show()

# ==================== 主程序入口 ====================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    config = Config()
    window = MainWindow(config)
    window.show()
    sys.exit(app.exec_())