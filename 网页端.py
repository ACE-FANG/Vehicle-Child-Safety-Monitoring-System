# remote_control_mobile_fixed.py - 修复线程安全问题的移动端版本

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
from io import BytesIO

# 第三方库导入
try:
    import paho.mqtt.client as mqtt
    from flask import Flask, render_template, request, jsonify, send_file, Response
    from flask_socketio import SocketIO
    from flask_cors import CORS
    import requests
    from PIL import Image
    print("所有依赖库导入成功")
except ImportError as e:
    print(f"缺少依赖库: {e}")
    print("请安装: pip install paho-mqtt pandas numpy flask flask-socketio flask-cors requests pillow")
    sys.exit(1)

# ==================== 配置类 ====================
class MobileConfig:
    """移动端配置"""
    # MQTT配置
    #MQTT_BROKER = "509pk6184bc5.vicp.fun"
    MQTT_BROKER = "broker.emqx.io"
    MQTT_PORT = 1883
    MQTT_USER = ""
    MQTT_PASSWORD = ""
    
    # 订阅和发布主题
    MQTT_TOPICS = {
        "status": "esp32/main/status",
        "child_detection": "esp32cam/child_detection",
        "control": "python/control",
        "sensor_data": "python/sensor_data",
        "alerts": "python/alerts",
        "captured_image": "python/captured_image",
    }
    
    # 远程访问配置
    DEVICE_ID = "vehicle_monitor_001"
    REMOTE_CONTROL_PASSWORD = "admin123"
    
    # 数据存储
    DB_PATH = "mobile_monitor.db"
    
    # Web服务器配置
    HOST = "0.0.0.0"
    PORT = 5000
    DEBUG = True  # 改为True以便调试

# ==================== 数据模型类 ====================
class MobileSensorData:
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

class MobileDetectionData:
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

# ==================== 移动端数据管理器 ====================
class MobileDataManager:
    """移动端数据管理"""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 移动端传感器数据表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mobile_sensor_data (
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
        CREATE TABLE IF NOT EXISTS mobile_control_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            command TEXT,
            params TEXT,
            result TEXT,
            remote_ip TEXT,
            operator TEXT DEFAULT 'remote'
        )
        ''')
        
        # 报警记录
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mobile_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            level TEXT,
            message TEXT,
            device_id TEXT,
            source TEXT DEFAULT 'device'
        )
        ''')
        
        # 抓拍图片表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mobile_captured_images (
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
        INSERT INTO mobile_sensor_data 
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
    
    def add_sensor_data(self, device_id: str, data: MobileSensorData):
        """添加传感器数据"""
        self.save_sensor_data(data.to_dict())
    
    def save_synced_alert(self, alert_data: dict):
        """保存同步报警"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO mobile_alerts 
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
        INSERT INTO mobile_control_history 
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
            SELECT * FROM mobile_sensor_data 
            WHERE device_id = ?
            ORDER BY timestamp DESC 
            LIMIT 1
            ''', (device_id,))
        else:
            cursor.execute('''
            SELECT * FROM mobile_sensor_data 
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
            SELECT * FROM mobile_sensor_data 
            WHERE device_id = ? AND received_time >= datetime('now', ?)
            ORDER BY timestamp ASC
            '''
            params = (device_id, f'-{hours} hours')
        else:
            query = '''
            SELECT * FROM mobile_sensor_data 
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
        SELECT * FROM mobile_alerts 
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
        SELECT * FROM mobile_control_history 
        ORDER BY timestamp DESC 
        LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        conn.close()
        return [dict(zip(columns, row)) for row in rows]
    
    def save_captured_image_record(self, data: dict):
        """保存抓拍图片记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO mobile_captured_images 
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
        SELECT * FROM mobile_captured_images 
        ORDER BY timestamp DESC 
        LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        conn.close()
        return [dict(zip(columns, row)) for row in rows]
    
    def get_image_by_id(self, image_id: int):
        """根据ID获取图片"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        SELECT * FROM mobile_captured_images 
        WHERE id = ?
        ''', (image_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None

# ==================== 线程安全的移动端MQTT管理器 ====================
class ThreadSafeMobileMQTTManager:
    """线程安全的移动端MQTT管理器"""
    
    def __init__(self, config: MobileConfig, data_manager: MobileDataManager, socketio):
        self.config = config
        self.data_manager = data_manager
        self.socketio = socketio
        
        # 创建MQTT客户端
        unique_id = str(uuid.uuid4())[:8]
        self.client = mqtt.Client(client_id=f"mobile_control_{unique_id}")
        
        if config.MQTT_USER and config.MQTT_PASSWORD:
            self.client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
        
        # 设置回调
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        
        # 连接状态
        self.is_connected = False
        
        # 使用线程安全的队列处理MQTT消息
        self.message_queue = queue.Queue()
        self.processing_thread = threading.Thread(target=self._process_messages, daemon=True)
        self.processing_thread.start()
        
        # 统计信息
        self.stats = {
            "messages_received": 0,
            "images_received": 0
        }
    
    def _process_messages(self):
        """在单独的线程中处理MQTT消息"""
        while True:
            try:
                message_data = self.message_queue.get(timeout=1)
                if message_data:
                    topic = message_data.get('topic')
                    data = message_data.get('data')
                    self._handle_message_in_thread(topic, data)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"处理消息队列错误: {e}")
                traceback.print_exc()
    
    def _handle_message_in_thread(self, topic, data):
        """在线程中处理消息"""
        try:
            if topic == self.config.MQTT_TOPICS["sensor_data"] or topic == self.config.MQTT_TOPICS["status"]:
                self.handle_sensor_data(data)
            elif topic == self.config.MQTT_TOPICS["alerts"]:
                self.handle_alert_data(data)
            elif topic == self.config.MQTT_TOPICS["captured_image"]:
                self.handle_captured_image(data)
            elif topic == self.config.MQTT_TOPICS["child_detection"]:
                self.handle_detection_data(data)
        except Exception as e:
            print(f"处理消息错误: {e}")
            traceback.print_exc()
    
    def connect(self):
        """连接到MQTT服务器"""
        try:
            print(f"尝试连接MQTT服务器: {self.config.MQTT_BROKER}:{self.config.MQTT_PORT}")
            self.client.connect(self.config.MQTT_BROKER, self.config.MQTT_PORT, 60)
            self.client.loop_start()
            return True
        except Exception as e:
            print(f"MQTT连接失败: {e}")
            return False
    
    def disconnect(self):
        """断开连接"""
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except:
            pass
        self.is_connected = False
    
    def on_connect(self, client, userdata, flags, rc):
        """连接成功回调"""
        if rc == 0:
            self.is_connected = True
            print("移动端MQTT连接成功")
            
            for topic_name, topic in self.config.MQTT_TOPICS.items():
                qos = 1 if topic_name in ["child_detection", "alerts", "captured_image"] else 0
                client.subscribe(topic, qos=qos)
                print(f"已订阅主题: {topic} (QoS: {qos})")
        else:
            print(f"MQTT连接失败，错误码: {rc}")
            self.is_connected = False
    
    def on_disconnect(self, client, userdata, rc):
        """断开连接回调"""
        self.is_connected = False
        print(f"MQTT连接断开，错误码: {rc}")
    
    def on_message(self, client, userdata, msg):
        """MQTT消息回调 - 仅将消息放入队列"""
        try:
            self.stats["messages_received"] += 1
            
            topic = msg.topic
            payload = msg.payload.decode()
            
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {"raw_message": payload}
            
            # 将消息放入队列，由单独的线程处理
            self.message_queue.put({
                'topic': topic,
                'data': data
            })
                
        except Exception as e:
            print(f"MQTT消息回调错误: {e}")
            traceback.print_exc()
    
    def handle_sensor_data(self, data: dict):
        """处理传感器数据"""
        try:
            # 创建传感器数据对象
            sensor_data = MobileSensorData(data)
            
            # 保存到数据库
            self.data_manager.add_sensor_data(sensor_data.device_id, sensor_data)
            
            # 使用SocketIO的线程安全方式发送实时更新
            self.socketio.emit('sensor_update', {
                'data': sensor_data.to_dict(),
                'timestamp': time.time()
            })
            
        except Exception as e:
            print(f"处理传感器数据错误: {e}")
    
    def handle_alert_data(self, data: dict):
        """处理报警数据"""
        try:
            # 保存报警
            self.data_manager.save_synced_alert(data)
            
            # 使用SocketIO的线程安全方式发送报警通知
            self.socketio.emit('alert_update', {
                'alert': data,
                'timestamp': time.time()
            })
            
            print(f"收到报警: {data.get('level')} - {data.get('message')}")
            
        except Exception as e:
            print(f"处理报警数据错误: {e}")
    
    def handle_captured_image(self, data: dict):
        """处理抓拍图片"""
        try:
            print(f"[INFO] 收到抓拍图片")
            
            image_base64 = data.get("image_base64")
            if not image_base64:
                print("[ERROR] 图片数据为空")
                return
            
            try:
                image_data = base64.b64decode(image_base64)
                print(f"[INFO] 图片解码成功，大小: {len(image_data)} 字节")
            except Exception as e:
                print(f"[ERROR] 图片解码失败: {e}")
                return
            
            # 保存图片到本地
            save_dir = self.config.CAPTURED_IMAGE_DIR
            
            # 确保目录存在
            if not os.path.exists(save_dir):
                print(f"[INFO] 创建图片目录: {save_dir}")
                os.makedirs(save_dir, exist_ok=True)
            
            # 生成文件名（确保安全）
            timestamp = datetime.fromtimestamp(data.get("timestamp", time.time())).strftime("%Y%m%d_%H%M%S")
            det_type = data.get("detection_type", "unknown")
            
            # 清理文件名
            import re
            det_type_clean = re.sub(r'[^\w\-_]', '_', det_type)
            
            filename = f"capture_{det_type_clean}_{timestamp}.jpg"
            filepath = os.path.join(save_dir, filename)
            
            # 检查文件是否已存在
            counter = 1
            while os.path.exists(filepath):
                filename = f"capture_{det_type_clean}_{timestamp}_{counter}.jpg"
                filepath = os.path.join(save_dir, filename)
                counter += 1
            
            try:
                with open(filepath, "wb") as f:
                    f.write(image_data)
                print(f"[SUCCESS] 抓拍图片已保存: {filepath}")
                print(f"[DEBUG] 文件大小: {os.path.getsize(filepath)} 字节")
            except Exception as e:
                print(f"[ERROR] 保存图片文件失败: {e}")
                return
            
            # 验证图片
            try:
                img = Image.open(BytesIO(image_data))
                print(f"[DEBUG] 图片验证: {img.size}, {img.format}")
                img.close()
            except Exception as e:
                print(f"[WARNING] 图片验证失败: {e}")

            image_data_for_db = None  # 或者只存储缩略图
            if len(image_data) > 1024 * 100:  # 超过100KB
                print(f"[INFO] 图片过大({len(image_data)}字节)，不存入数据库")
            
            # 更新数据记录
            data["local_path"] = filename
            data["image_data"] = image_data
            
            # 保存到数据库
            self.data_manager.save_captured_image_record(data)
            
            # 使用SocketIO的线程安全方式发送图片更新
            self.socketio.emit('image_update', {
                'image': {
                    'id': None,  # 数据库插入后会有ID
                    'filename': filename,
                    'timestamp': data.get("timestamp", time.time()),
                    'capture_time': data.get("capture_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    'child_count': data.get("child_count", 0),
                    'adult_count': data.get("adult_count", 0),
                    'confidence': data.get("confidence", 0),
                    'detection_type': data.get("detection_type", "unknown")
                },
                'timestamp': time.time()
            })
            
            print(f"[SUCCESS] 图片处理完成并发送到前端")
            
        except Exception as e:
            print(f"[ERROR] 处理抓拍图片错误: {e}")
            traceback.print_exc()
    
    def handle_detection_data(self, data: dict):
        """处理检测数据"""
        try:
            detection_data = MobileDetectionData(data)
            
            # 使用SocketIO的线程安全方式发送检测更新
            self.socketio.emit('detection_update', {
                'detection': detection_data.to_dict(),
                'timestamp': time.time()
            })
            
        except Exception as e:
            print(f"处理检测数据错误: {e}")
    
    def send_control_command(self, command: str, params: dict = None, operator: str = "mobile"):
        """发送控制命令"""
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
            
            # 保存控制历史
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
        """获取客户端IP"""
        try:
            import socket
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except:
            return "unknown"
    
    def get_stats(self):
        """获取统计信息"""
        return self.stats

# ==================== Flask Web应用 ====================
# 获取当前文件的目录
current_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(current_dir, 'static')
captured_images_dir = os.path.join(static_dir, 'captured_images')

# 确保目录存在
os.makedirs(static_dir, exist_ok=True)
os.makedirs(captured_images_dir, exist_ok=True)

print(f"[INFO] 静态文件夹: {static_dir}")
print(f"[INFO] 图片保存目录: {captured_images_dir}")

app = Flask(__name__, 
           template_folder='templates', 
           static_folder=static_dir,  # 明确指定static文件夹
           static_url_path='/static')  # 明确指定URL路径

app.config['SECRET_KEY'] = 'vehicle_monitor_mobile_secret_key'
CORS(app)

# 使用threading模式而不是eventlet
socketio = SocketIO(app, 
                    cors_allowed_origins="*", 
                    async_mode='threading',
                    logger=True,
                    engineio_logger=True)

# 全局变量
config = MobileConfig()
# 更新配置中的图片目录
config.CAPTURED_IMAGE_DIR = captured_images_dir
data_manager = MobileDataManager(config.DB_PATH)
mqtt_manager = None

# ==================== Flask路由 ====================
@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/routes')
def list_routes():
    """列出所有已注册的路由"""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            'endpoint': rule.endpoint,
            'methods': list(rule.methods),
            'rule': str(rule)
        })
    return jsonify({'routes': routes})

@app.route('/debug/images')
def debug_images():
    """调试图片目录"""
    import os
    image_dir = config.CAPTURED_IMAGE_DIR
    print(f"[DEBUG] 图片目录: {image_dir}")
    print(f"[DEBUG] 绝对路径: {os.path.abspath(image_dir)}")
    print(f"[DEBUG] 目录是否存在: {os.path.exists(image_dir)}")
    
    if os.path.exists(image_dir):
        files = os.listdir(image_dir)
        print(f"[DEBUG] 文件列表: {files}")
    else:
        files = []
        print(f"[DEBUG] 目录不存在，正在创建...")
        os.makedirs(image_dir, exist_ok=True)
    
    return jsonify({
        'dir_exists': os.path.exists(image_dir),
        'path': os.path.abspath(image_dir),
        'files': files[:10],
        'count': len(files)
    })

@app.route('/static/debug')
def static_debug():
    """静态文件调试"""
    import os
    static_path = app.static_folder
    return jsonify({
        'static_folder': static_path,
        'static_url_path': app.static_url_path,
        'captured_images_dir': config.CAPTURED_IMAGE_DIR,
        'captured_images_exists': os.path.exists(config.CAPTURED_IMAGE_DIR)
    })

@app.route('/test/image/<filename>')
def test_image(filename):
    """测试图片访问"""
    try:
        image_path = os.path.join(config.CAPTURED_IMAGE_DIR, filename)
        print(f"[DEBUG] 尝试访问图片: {image_path}")
        print(f"[DEBUG] 图片是否存在: {os.path.exists(image_path)}")
        
        if os.path.exists(image_path):
            return send_file(image_path, mimetype='image/jpeg')
        else:
            # 返回一个测试图片
            from PIL import Image
            import io
            
            # 创建一个简单的测试图片
            img = Image.new('RGB', (300, 200), color='red')
            img_io = io.BytesIO()
            img.save(img_io, 'JPEG')
            img_io.seek(0)
            
            return send_file(img_io, mimetype='image/jpeg', 
                           as_attachment=False, 
                           download_name='test.jpg')
            
    except Exception as e:
        print(f"[ERROR] 测试图片失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/data/latest')
def get_latest_data():
    """获取最新数据"""
    try:
        latest_data = data_manager.get_latest_data()
        if latest_data:
            return jsonify({
                'success': True,
                'data': latest_data,
                'timestamp': time.time()
            })
        else:
            return jsonify({
                'success': False,
                'message': '暂无数据'
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取数据失败: {str(e)}'
        })

@app.route('/api/alerts')
def get_alerts():
    """获取报警记录"""
    try:
        alerts = data_manager.get_recent_alerts(20)
        return jsonify({
            'success': True,
            'alerts': alerts
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取报警记录失败: {str(e)}'
        })

@app.route('/api/alerts/stats')
def get_alert_stats():
    """获取报警统计"""
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        # 总报警数
        cursor.execute("SELECT COUNT(*) FROM mobile_alerts")
        total = cursor.fetchone()[0]
        
        # 今日报警数
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            "SELECT COUNT(*) FROM mobile_captured_images WHERE date(received_time) = ?",
            (today,)
        )
        today_alerts = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'total': total,
            'today': today_alerts
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取报警统计失败: {str(e)}'
        })

@app.route('/api/alerts/clear', methods=['POST'])
def clear_alerts():
    """清空报警记录"""
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM mobile_alerts")
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': '报警记录已清空'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'清空报警记录失败: {str(e)}'
        })

@app.route('/api/captures')
def get_captures():
    """获取抓拍记录"""
    try:
        # 获取抓拍历史
        history = data_manager.get_captured_images_history(20)
        
        # 统计信息
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        # 总抓拍数
        cursor.execute("SELECT COUNT(*) FROM mobile_captured_images")
        total = cursor.fetchone()[0]
        
        # 今日抓拍数
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            "SELECT COUNT(*) FROM mobile_captured_images WHERE date(datetime(timestamp, 'unixepoch')) = ?",
            (today,)
        )
        today_captures = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'history': history,
            'stats': {
                'total': total,
                'today': today_captures
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取抓拍记录失败: {str(e)}'
        })

@app.route('/api/control', methods=['POST'])
def send_control():
    """发送控制命令"""
    try:
        data = request.json
        command = data.get('command')
        params = data.get('params', {})
        
        if not command:
            return jsonify({
                'success': False,
                'message': '未指定命令'
            })
        
        if not mqtt_manager:
            return jsonify({
                'success': False,
                'message': 'MQTT管理器未初始化'
            })
        
        success, message = mqtt_manager.send_control_command(command, params)
        
        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'发送命令失败: {str(e)}'
        })

@app.route('/api/control/history')
def get_control_history():
    """获取控制历史"""
    try:
        history = data_manager.get_control_history(20)
        return jsonify({
            'success': True,
            'history': history
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取控制历史失败: {str(e)}'
        })

@app.route('/api/chart/data')
def get_chart_data():
    """获取图表数据"""
    try:
        hours = request.args.get('hours', 24, type=int)
        df = data_manager.get_recent_data(hours=hours)
        
        if df.empty:
            return jsonify({
                'success': False,
                'message': '暂无数据'
            })
        
        # 格式化数据
        timestamps = df['timestamp'].tolist()
        temperatures = df['temperature'].tolist()
        humidities = df['humidity'].tolist()
        tvocs = df['tvoc'].tolist()
        eco2s = df['eco2'].tolist()
        
        # 转换为相对时间（分钟）
        if len(timestamps) > 0:
            base_time = timestamps[0]
            relative_times = [(t - base_time) / 60 for t in timestamps]
        else:
            relative_times = []
        
        return jsonify({
            'success': True,
            'data': {
                'timestamps': timestamps,
                'relative_times': relative_times,
                'temperatures': temperatures,
                'humidities': humidities,
                'tvocs': tvocs,
                'eco2s': eco2s
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取图表数据失败: {str(e)}'
        })

@app.route('/api/export')
def export_data():
    """导出数据"""
    try:
        format_type = request.args.get('format', 'csv')
        hours = request.args.get('hours', 24, type=int)
        
        df = data_manager.get_recent_data(hours=hours)
        
        if df.empty:
            return jsonify({
                'success': False,
                'message': '指定时间范围内没有数据'
            })
        
        # 根据格式导出
        if format_type == 'csv':
            csv_data = df.to_csv(index=False, encoding='utf-8')
            return Response(
                csv_data,
                mimetype='text/csv',
                headers={'Content-Disposition': 'attachment;filename=sensor_data.csv'}
            )
        elif format_type == 'json':
            json_data = df.to_json(orient='records', force_ascii=False, indent=2)
            return Response(
                json_data,
                mimetype='application/json',
                headers={'Content-Disposition': 'attachment;filename=sensor_data.json'}
            )
        else:
            return jsonify({
                'success': False,
                'message': f'不支持的格式: {format_type}'
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'导出数据失败: {str(e)}'
        })

@app.route('/api/health')
def health_check():
    """健康检查"""
    mqtt_connected = mqtt_manager.is_connected if mqtt_manager else False
    return jsonify({
        'status': 'ok',
        'timestamp': time.time(),
        'mqtt_connected': mqtt_connected
    })

# ==================== SocketIO事件 ====================
@socketio.on('connect')
def handle_connect():
    """客户端连接"""
    print(f'客户端已连接: {request.sid}')
    socketio.emit('connected', {'message': '连接成功', 'timestamp': time.time()})

@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开连接"""
    print(f'客户端断开连接: {request.sid}')

# ==================== 启动函数 ====================
def start_server():
    """启动Web服务器"""
    global mqtt_manager
    
    # 初始化MQTT管理器
    mqtt_manager = ThreadSafeMobileMQTTManager(config, data_manager, socketio)
    
    # 连接MQTT
    mqtt_connected = mqtt_manager.connect()
    if mqtt_connected:
        print("MQTT连接成功")
    else:
        print("MQTT连接失败，将在后台重试")
    
    # 确保模板目录存在
    templates_dir = "templates"
    os.makedirs(templates_dir, exist_ok=True)
    
    # 创建HTML模板
    HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes">
    <title>车载安全监控系统 - 移动端</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/viewerjs/1.11.6/viewer.min.css">
    <style>
        :root {
            --primary-color: #2c3e50;
            --secondary-color: #3498db;
            --success-color: #2ecc71;
            --warning-color: #f39c12;
            --danger-color: #e74c3c;
            --light-bg: #f8f9fa;
            --card-shadow: 0 4px 12px rgba(0,0,0,0.1);
            --border-radius: 12px;
        }
        
        * {
            -webkit-tap-highlight-color: transparent;
        }
        
        body {
            font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding-bottom: 80px;
            overflow-x: hidden;
        }
        
        .navbar {
            background: rgba(44, 62, 80, 0.95) !important;
            backdrop-filter: blur(10px);
            padding: 12px 0;
        }
        
        .card {
            border-radius: var(--border-radius);
            border: none;
            box-shadow: var(--card-shadow);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            margin-bottom: 16px;
            overflow: hidden;
        }
        
        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 8px 20px rgba(0,0,0,0.15);
        }
        
        .card-header {
            border-radius: var(--border-radius) var(--border-radius) 0 0 !important;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            font-weight: 600;
            padding: 15px 20px;
            font-size: 1.1rem;
        }
        
        .card-body {
            padding: 20px;
        }
        
        .value-display {
            font-size: 1.8rem;
            font-weight: bold;
            line-height: 1.2;
        }
        
        .value-unit {
            font-size: 0.9rem;
            color: #6c757d;
            font-weight: normal;
        }
        
        .risk-indicator {
            padding: 12px 24px;
            border-radius: 25px;
            color: white;
            text-align: center;
            font-weight: bold;
            font-size: 1.2rem;
            transition: all 0.3s;
            display: inline-block;
            min-width: 100px;
        }
        
        .risk-normal { 
            background-color: var(--success-color);
            box-shadow: 0 4px 12px rgba(46, 204, 113, 0.3);
        }
        .risk-warning { 
            background-color: var(--warning-color);
            box-shadow: 0 4px 12px rgba(243, 156, 18, 0.3);
        }
        .risk-emergency { 
            background-color: var(--danger-color);
            animation: pulse 2s infinite;
            box-shadow: 0 4px 12px rgba(231, 76, 60, 0.3);
        }
        
        .alert-item {
            padding: 12px 15px;
            margin-bottom: 8px;
            border-radius: 10px;
            border-left: 5px solid;
            animation: fadeIn 0.5s;
            transition: transform 0.2s;
        }
        
        .alert-item:hover {
            transform: translateX(5px);
        }
        
        .alert-emergency {
            background-color: #ffeaea;
            border-left-color: var(--danger-color);
        }
        
        .alert-warning {
            background-color: #fff4e6;
            border-left-color: var(--warning-color);
        }
        
        .alert-info {
            background-color: #e6f7ff;
            border-left-color: var(--secondary-color);
        }
        
        .btn-control {
            padding: 14px;
            margin: 5px;
            border-radius: 12px;
            font-weight: 600;
            transition: all 0.3s;
            border: none;
            font-size: 1rem;
        }
        
        .btn-control:active {
            transform: scale(0.95);
        }
        
        .btn-control i {
            margin-right: 8px;
        }
        
        .status-badge {
            display: inline-block;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
        }
        
        .status-online {
            background-color: var(--success-color);
            color: white;
        }
        
        .status-offline {
            background-color: #95a5a6;
            color: white;
        }
        
        /* 图片显示优化 */
        .image-container {
            width: 100%;
            background-color: #f8f9fa;
            border-radius: 10px;
            overflow: hidden;
            position: relative;
            padding-top: 75%; /* 4:3 比例 */
            margin-bottom: 15px;
        }
        
        .captured-image {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: contain; /* 改为contain确保完整显示 */
            transition: transform 0.3s;
            cursor: pointer;
        }
        
        .captured-image:hover {
            transform: scale(1.02);
        }
        
        .image-info {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            background: linear-gradient(transparent, rgba(0,0,0,0.7));
            color: white;
            padding: 15px;
            transform: translateY(100%);
            transition: transform 0.3s;
        }
        
        .image-container:hover .image-info {
            transform: translateY(0);
        }
        
        .tab-content {
            animation: fadeIn 0.5s;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        @keyframes pulse {
            0% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.05); opacity: 0.8; }
            100% { transform: scale(1); opacity: 1; }
        }
        
        /* 移动端优化 */
        @media (max-width: 768px) {
            .value-display { 
                font-size: 1.6rem; 
            }
            
            .card { 
                margin-bottom: 12px;
                border-radius: 10px;
            }
            
            .card-header {
                padding: 12px 15px;
                font-size: 1rem;
            }
            
            .card-body {
                padding: 15px;
            }
            
            .btn-control { 
                padding: 12px;
                font-size: 0.95rem;
                margin: 4px;
            }
            
            .container {
                padding-left: 10px;
                padding-right: 10px;
            }
            
            .image-container {
                padding-top: 66.67%; /* 3:2 比例 */
            }
        }
        
        @media (max-width: 480px) {
            .value-display { 
                font-size: 1.4rem; 
            }
            
            .risk-indicator {
                padding: 10px 20px;
                font-size: 1rem;
                min-width: 80px;
            }
            
            .image-container {
                padding-top: 56.25%; /* 16:9 比例 */
            }
        }
        
        /* 底部导航栏优化 */
        .bottom-nav {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: rgba(44, 62, 80, 0.95);
            backdrop-filter: blur(10px);
            padding: 10px 0;
            display: flex;
            justify-content: space-around;
            z-index: 1000;
            border-top: 1px solid rgba(255,255,255,0.1);
        }
        
        .nav-item {
            color: #bdc3c7;
            text-align: center;
            padding: 8px 5px;
            border-radius: 10px;
            transition: all 0.3s;
            flex: 1;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 50px;
        }
        
        .nav-item.active {
            color: white;
            background: rgba(52, 152, 219, 0.3);
            transform: translateY(-5px);
        }
        
        .nav-item i {
            font-size: 1.3rem;
            margin-bottom: 4px;
            transition: transform 0.3s;
        }
        
        .nav-item.active i {
            transform: scale(1.1);
        }
        
        .nav-item span {
            font-size: 0.75rem;
            display: block;
        }
        
        /* 数据卡片优化 */
        .data-card {
            background: white;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        
        .data-label {
            font-size: 0.85rem;
            color: #6c757d;
            margin-bottom: 5px;
            font-weight: 500;
        }
        
        .data-value {
            font-size: 1.4rem;
            font-weight: 600;
            color: #2c3e50;
        }
        
        /* 滚动条优化 */
        ::-webkit-scrollbar {
            width: 6px;
        }
        
        ::-webkit-scrollbar-track {
            background: #f1f1f1;
            border-radius: 10px;
        }
        
        ::-webkit-scrollbar-thumb {
            background: #c1c1c1;
            border-radius: 10px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: #a8a8a8;
        }
        
        /* 加载动画 */
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(52, 152, 219, 0.3);
            border-radius: 50%;
            border-top-color: #3498db;
            animation: spin 1s ease-in-out infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        /* 统计卡片 */
        .stat-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 15px;
            padding: 20px;
            text-align: center;
            margin-bottom: 15px;
        }
        
        .stat-value {
            font-size: 2rem;
            font-weight: bold;
            margin-bottom: 5px;
        }
        
        .stat-label {
            font-size: 0.9rem;
            opacity: 0.9;
        }
        
        /* 模态框样式 */
        .modal-image {
            max-width: 100%;
            height: auto;
            border-radius: 10px;
        }
        
        /* 触摸反馈 */
        .touch-feedback:active {
            opacity: 0.7;
        }
    </style>
</head>
<body>
    <!-- 顶部导航栏 -->
    <nav class="navbar navbar-dark sticky-top">
        <div class="container-fluid">
            <span class="navbar-brand">
                <i class="fas fa-car me-2"></i>
                车载安全监控
            </span>
            <div class="d-flex align-items-center">
                <span id="connection-status" class="status-badge status-offline me-2">离线</span>
                <span id="update-time" class="text-light" style="font-size: 0.85rem;">--:--:--</span>
            </div>
        </div>
    </nav>
    
    <!-- 主内容区 -->
    <div class="container mt-3" id="main-content">
        <!-- 仪表盘页面 -->
        <div id="dashboard-page" class="page">
            <!-- 风险状态卡片 -->
            <div class="card touch-feedback">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span><i class="fas fa-exclamation-triangle me-2"></i>风险状态</span>
                    <span id="risk-timestamp" class="text-light" style="font-size: 0.8rem;">--:--:--</span>
                </div>
                <div class="card-body text-center">
                    <div id="risk-indicator" class="risk-indicator risk-normal mb-3">
                        <i class="fas fa-check-circle me-2"></i>正常
                    </div>
                    <p id="risk-description" class="text-muted mb-0">系统运行正常</p>
                </div>
            </div>
            
            <!-- 环境监测卡片 -->
            <div class="card touch-feedback">
                <div class="card-header">
                    <i class="fas fa-thermometer-half me-2"></i>环境监测
                </div>
                <div class="card-body">
                    <div class="row g-3">
                        <div class="col-6">
                            <div class="data-card text-center">
                                <div class="data-label">温度</div>
                                <div class="data-value text-danger" id="temperature-value">--</div>
                                <div class="value-unit">°C</div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="data-card text-center">
                                <div class="data-label">湿度</div>
                                <div class="data-value text-primary" id="humidity-value">--</div>
                                <div class="value-unit">%</div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="data-card text-center">
                                <div class="data-label">CO₂</div>
                                <div class="data-value text-success" id="co2-value">--</div>
                                <div class="value-unit">ppm</div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="data-card text-center">
                                <div class="data-label">TVOC</div>
                                <div class="data-value text-warning" id="tvoc-value">--</div>
                                <div class="value-unit">ppb</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- 系统状态卡片 -->
            <div class="card touch-feedback">
                <div class="card-header">
                    <i class="fas fa-car me-2"></i>系统状态
                </div>
                <div class="card-body">
                    <div class="row g-2">
                        <div class="col-6">
                            <div class="data-card">
                                <div class="data-label">车门状态</div>
                                <div class="data-value" id="door-status-value">--</div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="data-card">
                                <div class="data-label">运动检测</div>
                                <div class="data-value" id="pir-status-value">--</div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="data-card">
                                <div class="data-label">成人检测</div>
                                <div class="data-value" id="human-status-value">--</div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="data-card">
                                <div class="data-label">儿童检测</div>
                                <div class="data-value" id="child-status-value">--</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- 控制中心页面 -->
        <div id="control-page" class="page" style="display: none;">
            <div class="card">
                <div class="card-header">
                    <i class="fas fa-gamepad me-2"></i>远程控制
                </div>
                <div class="card-body">
                    <div class="row g-2">
                        <div class="col-6">
                            <button class="btn btn-primary btn-control w-100 touch-feedback" onclick="sendCommand('lower_window', {percent: 100})">
                                <i class="fas fa-window-maximize me-2"></i>一键降窗
                            </button>
                        </div>
                        <div class="col-6">
                            <button class="btn btn-warning btn-control w-100 touch-feedback" onclick="sendCommand('test_alarm', {})">
                                <i class="fas fa-bell me-2"></i>测试报警
                            </button>
                        </div>
                        <div class="col-6">
                            <button class="btn btn-info btn-control w-100 touch-feedback" onclick="sendCommand('send_sms', {})">
                                <i class="fas fa-sms me-2"></i>发送短信
                            </button>
                        </div>
                        <div class="col-6">
                            <button class="btn btn-danger btn-control w-100 touch-feedback" onclick="resetSystem()">
                                <i class="fas fa-redo me-2"></i>系统复位
                            </button>
                        </div>
                        <div class="col-6">
                            <button class="btn btn-secondary btn-control w-100 touch-feedback" onclick="sendCommand('close_window', {})">
                                <i class="fas fa-window-minimize me-2"></i>一键关窗
                            </button>
                        </div>
                        <div class="col-6">
                            <button class="btn btn-success btn-control w-100 touch-feedback" onclick="refreshData()">
                                <i class="fas fa-sync-alt me-2"></i>刷新数据
                            </button>
                        </div>
                    </div>
                    
                    <div class="mt-4">
                        <h6><i class="fas fa-history me-2"></i>控制历史</h6>
                        <div id="control-history" class="list-group" style="max-height: 200px; overflow-y: auto;">
                            <!-- 控制历史记录会动态添加到这里 -->
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- 报警管理页面 -->
        <div id="alerts-page" class="page" style="display: none;">
            <div class="card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span><i class="fas fa-bell me-2"></i>报警记录</span>
                    <div>
                        <button class="btn btn-sm btn-outline-light touch-feedback" onclick="clearAlerts()" title="清空报警">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
                <div class="card-body">
                    <div class="row mb-3">
                        <div class="col-6">
                            <div class="stat-card">
                                <div class="stat-value" id="total-alerts">0</div>
                                <div class="stat-label">总报警数</div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="stat-card">
                                <div class="stat-value" id="today-alerts">0</div>
                                <div class="stat-label">今日报警</div>
                            </div>
                        </div>
                    </div>
                    
                    <h6 class="mb-3"><i class="fas fa-list me-2"></i>最近报警记录</h6>
                    <div id="alerts-list" style="max-height: 300px; overflow-y: auto;">
                        <!-- 报警记录会动态添加到这里 -->
                        <div class="text-center text-muted py-5" id="no-alerts">
                            <i class="fas fa-check-circle fa-3x mb-3" style="color: #2ecc71;"></i>
                            <p>暂无报警记录</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- 抓拍记录页面 -->
        <div id="capture-page" class="page" style="display: none;">
            <div class="card">
                <div class="card-header">
                    <i class="fas fa-camera me-2"></i>人员抓拍记录
                </div>
                <div class="card-body">
                    <h6 class="mb-3"><i class="fas fa-image me-2"></i>最新抓拍</h6>
                    <div id="latest-capture" class="text-center">
                        <div class="text-muted py-5">
                            <i class="fas fa-camera fa-3x mb-3" style="color: #95a5a6;"></i>
                            <p>等待抓拍图片...</p>
                        </div>
                    </div>
                    
                    <h6 class="mt-4 mb-3"><i class="fas fa-history me-2"></i>历史记录</h6>
                    <div id="capture-history" style="max-height: 300px; overflow-y: auto;">
                        <!-- 抓拍历史记录会动态添加到这里 -->
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- 底部导航栏 -->
    <div class="bottom-nav">
        <div class="nav-item active" onclick="showPage('dashboard')">
            <i class="fas fa-tachometer-alt"></i>
            <span>仪表盘</span>
        </div>
        <div class="nav-item" onclick="showPage('control')">
            <i class="fas fa-gamepad"></i>
            <span>控制</span>
        </div>
        <div class="nav-item" onclick="showPage('alerts')">
            <i class="fas fa-bell"></i>
            <span>报警</span>
        </div>
        <div class="nav-item" onclick="showPage('capture')">
            <i class="fas fa-camera"></i>
            <span>抓拍</span>
        </div>
    </div>
    
    <!-- 图片查看模态框 -->
    <div class="modal fade" id="imageModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">查看图片</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body text-center">
                    <img id="modalImage" src="" class="modal-image w-100" alt="查看图片">
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                    <a id="downloadImage" href="#" class="btn btn-primary" download>
                        <i class="fas fa-download me-2"></i>下载图片
                    </a>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Bootstrap JS -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <!-- SocketIO -->
    <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    <!-- Viewer.js for better image viewing -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/viewerjs/1.11.6/viewer.min.js"></script>
    
    <script>
        // 全局变量
        let socket = null;
        let currentData = null;
        let currentRiskLevel = 'normal';
        let imageViewer = null;
        
        // 页面切换函数
        function showPage(pageName) {
            // 隐藏所有页面
            document.querySelectorAll('.page').forEach(page => {
                page.style.display = 'none';
            });
            
            // 显示目标页面
            document.getElementById(pageName + '-page').style.display = 'block';
            
            // 更新导航栏激活状态
            document.querySelectorAll('.nav-item').forEach(item => {
                item.classList.remove('active');
            });
            
            // 根据页面名称找到对应的导航项
            const pageMap = {
                'dashboard': 0,
                'control': 1,
                'alerts': 2,
                'capture': 3
            };
            
            if (pageMap[pageName] !== undefined) {
                document.querySelectorAll('.nav-item')[pageMap[pageName]].classList.add('active');
            }
            
            // 如果是抓拍页面，加载抓拍历史
            if (pageName === 'capture') {
                loadCaptureHistory();
            }
        }
        
        // 初始化SocketIO连接
        function initSocket() {
            socket = io();
            
            socket.on('connect', function() {
                console.log('已连接到服务器');
                updateConnectionStatus(true);
            });
            
            socket.on('disconnect', function() {
                console.log('与服务器断开连接');
                updateConnectionStatus(false);
            });
            
            // 传感器数据更新
            socket.on('sensor_update', function(data) {
                console.log('收到传感器更新:', data);
                updateSensorDisplay(data.data);
                updateConnectionStatus(true);
            });
            
            // 报警更新
            socket.on('alert_update', function(data) {
                console.log('收到报警更新:', data);
                addAlertToList(data.alert);
                updateAlertStats();
                updateRiskFromAlert(data.alert);
            });
            
            // 图片更新
            socket.on('image_update', function(data) {
                console.log('收到图片更新:', data);
                updateCaptureDisplay(data.image);
                loadCaptureHistory();
            });
            
            // 检测更新
            socket.on('detection_update', function(data) {
                console.log('收到检测更新:', data);
                updateDetectionDisplay(data.detection);
            });
        }
        
        // 更新连接状态
        function updateConnectionStatus(connected) {
            const statusElement = document.getElementById('connection-status');
            if (connected) {
                statusElement.textContent = '在线';
                statusElement.className = 'status-badge status-online';
            } else {
                statusElement.textContent = '离线';
                statusElement.className = 'status-badge status-offline';
            }
        }
        
        // 更新传感器数据显示
        function updateSensorDisplay(data) {
            if (!data) return;
            
            // 更新时间显示
            const now = new Date();
            document.getElementById('update-time').textContent = 
                now.getHours().toString().padStart(2, '0') + ':' +
                now.getMinutes().toString().padStart(2, '0') + ':' +
                now.getSeconds().toString().padStart(2, '0');
            
            // 更新温度
            if (data.temperature !== undefined) {
                document.getElementById('temperature-value').textContent = data.temperature.toFixed(1);
            }
            
            // 更新湿度
            if (data.humidity !== undefined) {
                document.getElementById('humidity-value').textContent = data.humidity.toFixed(1);
            }
            
            // 更新CO2
            if (data.eco2 !== undefined) {
                document.getElementById('co2-value').textContent = data.eco2;
            }
            
            // 更新TVOC
            if (data.tvoc !== undefined) {
                document.getElementById('tvoc-value').textContent = data.tvoc;
            }
            
            // 更新车门状态
            if (data.door_closed !== undefined) {
                const doorText = data.door_closed ? '关闭' : '打开';
                const doorColor = data.door_closed ? 'text-success' : 'text-danger';
                document.getElementById('door-status-value').textContent = doorText;
                document.getElementById('door-status-value').className = 'data-value ' + doorColor;
            }
            
            // 更新PIR状态
            if (data.pir_state !== undefined) {
                const pirText = data.pir_state ? '检测到' : '无';
                const pirColor = data.pir_state ? 'text-warning' : 'text-secondary';
                document.getElementById('pir-status-value').textContent = pirText;
                document.getElementById('pir-status-value').className = 'data-value ' + pirColor;
            }
            
            // 更新成人检测
            if (data.human_detected !== undefined) {
                const humanText = data.human_detected ? '检测到' : '无';
                const humanColor = data.human_detected ? 'text-primary' : 'text-secondary';
                document.getElementById('human-status-value').textContent = humanText;
                document.getElementById('human-status-value').className = 'data-value ' + humanColor;
            }
            
            // 更新儿童检测
            if (data.child_detected !== undefined) {
                const childText = data.child_detected ? '检测到' : '无';
                const childColor = data.child_detected ? 'text-danger' : 'text-secondary';
                document.getElementById('child-status-value').textContent = childText;
                document.getElementById('child-status-value').className = 'data-value ' + childColor;
            }
            
            // 保存当前数据
            currentData = data;
        }
        
        // 更新风险状态
        function updateRiskFromAlert(alert) {
            if (!alert) return;
            
            const riskElement = document.getElementById('risk-indicator');
            const descElement = document.getElementById('risk-description');
            const timestampElement = document.getElementById('risk-timestamp');
            
            let riskLevel = 'normal';
            let riskText = '正常';
            let riskClass = 'risk-normal';
            let description = '系统运行正常';
            let icon = 'fa-check-circle';
            
            // 根据报警等级确定风险状态
            if (alert.level === 'emergency') {
                riskLevel = 'emergency';
                riskText = '紧急';
                riskClass = 'risk-emergency';
                description = alert.message || '系统检测到紧急情况！';
                icon = 'fa-exclamation-circle';
            } else if (alert.level === 'warning') {
                riskLevel = 'warning';
                riskText = '警告';
                riskClass = 'risk-warning';
                description = alert.message || '系统检测到警告情况';
                icon = 'fa-exclamation-triangle';
            }
            
            // 更新显示
            riskElement.innerHTML = `<i class="fas ${icon} me-2"></i>${riskText}`;
            riskElement.className = 'risk-indicator ' + riskClass;
            descElement.textContent = description;
            
            // 更新时间戳
            if (alert.timestamp) {
                const date = new Date(alert.timestamp * 1000);
                timestampElement.textContent = 
                    date.getHours().toString().padStart(2, '0') + ':' +
                    date.getMinutes().toString().padStart(2, '0') + ':' +
                    date.getSeconds().toString().padStart(2, '0');
            }
            
            currentRiskLevel = riskLevel;
        }
        
        // 添加报警到列表
        function addAlertToList(alert) {
            if (!alert) return;
            
            // 隐藏"无报警"提示
            const noAlertsElement = document.getElementById('no-alerts');
            if (noAlertsElement) {
                noAlertsElement.style.display = 'none';
            }
            
            const alertsList = document.getElementById('alerts-list');
            
            // 创建报警项
            const alertItem = document.createElement('div');
            
            // 确定报警样式
            let alertClass = 'alert-info';
            let iconClass = 'fa-info-circle';
            if (alert.level === 'emergency') {
                alertClass = 'alert-emergency';
                iconClass = 'fa-exclamation-circle';
            } else if (alert.level === 'warning') {
                alertClass = 'alert-warning';
                iconClass = 'fa-exclamation-triangle';
            }
            
            // 格式化时间
            let timeStr = '--:--:--';
            if (alert.timestamp) {
                const date = new Date(alert.timestamp * 1000);
                timeStr = 
                    date.getHours().toString().padStart(2, '0') + ':' +
                    date.getMinutes().toString().padStart(2, '0') + ':' +
                    date.getSeconds().toString().padStart(2, '0');
            }
            
            alertItem.className = 'alert-item ' + alertClass;
            alertItem.innerHTML = `
                <div class="d-flex justify-content-between align-items-start">
                    <div>
                        <i class="fas ${iconClass} me-2"></i>
                        <strong>${alert.level || 'INFO'}</strong>
                    </div>
                    <small class="text-muted">${timeStr}</small>
                </div>
                <div class="mt-1 small">${alert.message || ''}</div>
            `;
            
            // 添加到列表顶部
            alertsList.insertBefore(alertItem, alertsList.firstChild);
            
            // 限制列表长度
            const maxAlerts = 20;
            const alerts = alertsList.querySelectorAll('.alert-item');
            if (alerts.length > maxAlerts) {
                alerts[alerts.length - 1].remove();
            }
            
            // 播放提示音（如果是紧急或警告报警）
            if (alert.level === 'emergency' || alert.level === 'warning') {
                playAlertSound();
            }
        }
        
        // 播放报警提示音
        function playAlertSound() {
            try {
                // 创建简单的提示音
                const audioContext = new (window.AudioContext || window.webkitAudioContext)();
                const oscillator = audioContext.createOscillator();
                const gainNode = audioContext.createGain();
                
                oscillator.connect(gainNode);
                gainNode.connect(audioContext.destination);
                
                oscillator.frequency.value = 800;
                oscillator.type = 'sine';
                
                gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);
                
                oscillator.start(audioContext.currentTime);
                oscillator.stop(audioContext.currentTime + 0.5);
            } catch (e) {
                console.log('无法播放报警音:', e);
            }
        }
        
        // 更新报警统计
        function updateAlertStats() {
            fetch('/api/alerts/stats')
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('total-alerts').textContent = data.total || 0;
                        document.getElementById('today-alerts').textContent = data.today || 0;
                    }
                })
                .catch(error => {
                    console.error('获取报警统计失败:', error);
                });
        }
        
        // 清空报警记录
        function clearAlerts() {
            if (confirm('确定要清空所有报警记录吗？')) {
                fetch('/api/alerts/clear', { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            document.getElementById('alerts-list').innerHTML = `
                                <div class="text-center text-muted py-5" id="no-alerts">
                                    <i class="fas fa-check-circle fa-3x mb-3" style="color: #2ecc71;"></i>
                                    <p>暂无报警记录</p>
                                </div>
                            `;
                            document.getElementById('total-alerts').textContent = '0';
                            document.getElementById('today-alerts').textContent = '0';
                            
                            // 风险状态恢复为正常
                            updateRiskFromAlert({ level: 'normal', message: '系统运行正常' });
                        }
                    })
                    .catch(error => {
                        console.error('清空报警失败:', error);
                        alert('清空报警失败: ' + error);
                    });
            }
        }
        
        // 发送控制命令
        function sendCommand(command, params) {
            if (!socket || !socket.connected) {
                alert('未连接到服务器，无法发送命令');
                return;
            }
            
            if (confirm(`确定要执行 ${command} 命令吗？`)) {
                fetch('/api/control', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        command: command,
                        params: params || {}
                    })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 添加到控制历史
                        addControlHistory(command, params, '成功');
                        alert('命令发送成功！');
                    } else {
                        addControlHistory(command, params, '失败: ' + data.message);
                        alert('命令发送失败: ' + data.message);
                    }
                })
                .catch(error => {
                    console.error('发送命令失败:', error);
                    addControlHistory(command, params, '失败: ' + error);
                    alert('发送命令失败: ' + error);
                });
            }
        }
        
        // 系统复位
        function resetSystem() {
            if (confirm('确定要复位系统吗？这将停止所有报警并重置系统状态。')) {
                sendCommand('reset_system', {});
                
                // 本地立即更新风险状态为正常
                updateRiskFromAlert({ level: 'normal', message: '系统已复位，所有报警状态已停止' });
            }
        }
        
        // 添加控制历史
        function addControlHistory(command, params, result) {
            const historyList = document.getElementById('control-history');
            
            const historyItem = document.createElement('div');
            historyItem.className = 'list-group-item list-group-item-action';
            
            const now = new Date();
            const timeStr = now.getHours().toString().padStart(2, '0') + ':' +
                           now.getMinutes().toString().padStart(2, '0') + ':' +
                           now.getSeconds().toString().padStart(2, '0');
            
            let paramsStr = '';
            if (params && Object.keys(params).length > 0) {
                paramsStr = ' (' + JSON.stringify(params) + ')';
            }
            
            historyItem.innerHTML = `
                <div class="d-flex w-100 justify-content-between">
                    <small>${timeStr}</small>
                    <small class="${result.includes('成功') ? 'text-success' : 'text-danger'}">${result}</small>
                </div>
                <p class="mb-1 small">${command}${paramsStr}</p>
            `;
            
            // 添加到列表顶部
            historyList.insertBefore(historyItem, historyList.firstChild);
            
            // 限制列表长度
            const maxHistory = 10;
            const historyItems = historyList.querySelectorAll('.list-group-item');
            if (historyItems.length > maxHistory) {
                historyItems[historyItems.length - 1].remove();
            }
        }
        
        // 更新抓拍显示 - 修复图片显示问题
        function updateCaptureDisplay(image) {
            if (!image) return;
            
            const captureContainer = document.getElementById('latest-capture');
            
            // 格式化时间
            let timeStr = image.capture_time || '--:--:--';
            if (image.timestamp) {
                const date = new Date(image.timestamp * 1000);
                timeStr = 
                    date.getHours().toString().padStart(2, '0') + ':' +
                    date.getMinutes().toString().padStart(2, '0') + ':' +
                    date.getSeconds().toString().padStart(2, '0');
            }
            
            // 使用新的图片容器布局
            captureContainer.innerHTML = `
                <div class="image-container" onclick="viewImageModal('${image.filename}')">
                    <img src="/static/captured_images/${image.filename}" 
                         class="captured-image" 
                         alt="抓拍图片"
                         onerror="this.onerror=null; this.src='data:image/svg+xml;utf8,<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"300\" height=\"225\"><rect width=\"100%\" height=\"100%\" fill=\"%23f8f9fa\"/><text x=\"50%\" y=\"50%\" font-family=\"Arial\" font-size=\"16\" fill=\"%236c757d\" text-anchor=\"middle\" dy=\".3em\">图片加载失败</text></svg>'">
                    <div class="image-info">
                        <div class="row">
                            <div class="col-6">
                                <small><i class="far fa-clock me-1"></i>${timeStr}</small>
                            </div>
                            <div class="col-6 text-end">
                                <small><i class="fas fa-child me-1"></i>${image.child_count || 0}</small>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="row mt-3">
                    <div class="col-6">
                        <div class="data-card text-center">
                            <div class="data-label">检测类型</div>
                            <div class="data-value">${image.detection_type || '未知'}</div>
                        </div>
                    </div>
                    <div class="col-6">
                        <div class="data-card text-center">
                            <div class="data-label">置信度</div>
                            <div class="data-value">${(image.confidence || 0).toFixed(1)}%</div>
                        </div>
                    </div>
                </div>
                <div class="d-grid gap-2 mt-3">
                    <button class="btn btn-primary" onclick="viewImageModal('${image.filename}')">
                        <i class="fas fa-expand me-2"></i>查看大图
                    </button>
                    <button class="btn btn-outline-primary" onclick="downloadImage('${image.filename}')">
                        <i class="fas fa-download me-2"></i>下载图片
                    </button>
                </div>
            `;
            
            // 初始化图片查看器
            initImageViewer();
        }
        
        // 初始化图片查看器
        function initImageViewer() {
            if (imageViewer) {
                imageViewer.destroy();
            }
            
            const images = document.querySelectorAll('.captured-image');
            if (images.length > 0) {
                imageViewer = new Viewer(images[0], {
                    inline: false,
                    toolbar: {
                        zoomIn: 1,
                        zoomOut: 1,
                        oneToOne: 1,
                        reset: 1,
                        prev: 0,
                        play: 0,
                        next: 0,
                        rotateLeft: 1,
                        rotateRight: 1,
                        flipHorizontal: 1,
                        flipVertical: 1,
                    },
                    viewed() {
                        imageViewer.zoomTo(1);
                    }
                });
            }
        }
        
        // 查看图片模态框
        function viewImageModal(filename) {
            const modalImage = document.getElementById('modalImage');
            const downloadLink = document.getElementById('downloadImage');
            
            modalImage.src = `/static/captured_images/${filename}`;
            downloadLink.href = `/static/captured_images/${filename}`;
            downloadLink.download = filename;
            
            const modal = new bootstrap.Modal(document.getElementById('imageModal'));
            modal.show();
        }
        
        // 下载图片
        function downloadImage(filename) {
            const link = document.createElement('a');
            link.href = `/static/captured_images/${filename}`;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
        
        // 加载抓拍历史
        function loadCaptureHistory() {
            fetch('/api/captures')
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 更新历史记录
                        const historyContainer = document.getElementById('capture-history');
                        if (data.history && data.history.length > 0) {
                            let historyHTML = '';
                            
                            data.history.forEach((capture, index) => {
                                if (index >= 10) return; // 只显示最近10条
                                
                                const timeStr = capture.capture_time || '--:--:--';
                                const date = new Date(capture.timestamp * 1000);
                                const displayTime = date.toLocaleTimeString();
                                
                                historyHTML += `
                                    <div class="list-group-item mb-2" style="border-radius: 10px;">
                                        <div class="d-flex align-items-center">
                                            <div class="flex-shrink-0 me-3">
                                                <div style="width: 60px; height: 60px; border-radius: 8px; overflow: hidden; background-color: #f8f9fa;">
                                                    <img src="/static/captured_images/${capture.local_path}" 
                                                         style="width: 100%; height: 100%; object-fit: cover;"
                                                         onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"60\" height=\"60\"><rect width=\"100%\" height=\"100%\" fill=\"%23f8f9fa\"/><text x=\"50%\" y=\"50%\" font-family=\"Arial\" font-size=\"12\" fill=\"%236c757d\" text-anchor=\"middle\" dy=\".3em\">无图</text></svg>'">
                                                </div>
                                            </div>
                                            <div class="flex-grow-1">
                                                <div class="d-flex justify-content-between align-items-center">
                                                    <strong>抓拍记录</strong>
                                                    <small class="text-muted">${displayTime}</small>
                                                </div>
                                                <div class="mt-1">
                                                    <span class="badge bg-primary me-2">
                                                        <i class="fas fa-child me-1"></i>${capture.child_count || 0}
                                                    </span>
                                                    <span class="badge bg-secondary me-2">
                                                        <i class="fas fa-user me-1"></i>${capture.adult_count || 0}
                                                    </span>
                                                    <span class="badge bg-info">
                                                        <i class="fas fa-chart-line me-1"></i>${(capture.confidence || 0).toFixed(0)}%
                                                    </span>
                                                </div>
                                            </div>
                                            <div class="flex-shrink-0 ms-3">
                                                <button class="btn btn-sm btn-outline-primary" onclick="viewImageModal('${capture.local_path}')">
                                                    <i class="fas fa-eye"></i>
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                `;
                            });
                            
                            historyContainer.innerHTML = historyHTML;
                        } else {
                            historyContainer.innerHTML = `
                                <div class="text-center text-muted py-4">
                                    <i class="fas fa-camera fa-2x mb-3" style="color: #95a5a6;"></i>
                                    <p>暂无抓拍记录</p>
                                </div>
                            `;
                        }
                    }
                })
                .catch(error => {
                    console.error('加载抓拍历史失败:', error);
                });
        }
        
        // 更新检测显示
        function updateDetectionDisplay(detection) {
            // 这里可以添加检测数据的显示逻辑
            console.log('检测数据更新:', detection);
        }
        
        // 刷新数据
        function refreshData() {
            fetch('/api/data/latest')
                .then(response => response.json())
                .then(data => {
                    if (data.success && data.data) {
                        updateSensorDisplay(data.data);
                        showToast('数据刷新成功！', 'success');
                    } else {
                        showToast('刷新数据失败', 'error');
                    }
                })
                .catch(error => {
                    console.error('刷新数据失败:', error);
                    showToast('刷新数据失败: ' + error, 'error');
                });
        }
        
        // 显示Toast消息
        function showToast(message, type = 'info') {
            const toast = document.createElement('div');
            toast.className = `toast align-items-center text-white bg-${type === 'success' ? 'success' : type === 'error' ? 'danger' : 'info'} border-0`;
            toast.setAttribute('role', 'alert');
            toast.setAttribute('aria-live', 'assertive');
            toast.setAttribute('aria-atomic', 'true');
            toast.innerHTML = `
                <div class="d-flex">
                    <div class="toast-body">
                        ${message}
                    </div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
                </div>
            `;
            
            document.body.appendChild(toast);
            const bsToast = new bootstrap.Toast(toast);
            bsToast.show();
            
            toast.addEventListener('hidden.bs.toast', () => {
                document.body.removeChild(toast);
            });
        }
        
        // 页面加载完成后初始化
        document.addEventListener('DOMContentLoaded', function() {
            // 初始化Socket连接
            initSocket();
            
            // 显示仪表盘页面
            showPage('dashboard');
            
            // 加载初始数据
            refreshData();
            updateAlertStats();
            loadCaptureHistory();
            
            // 自动刷新数据（每30秒）
            setInterval(refreshData, 30000);
            
            // 自动刷新报警统计（每60秒）
            setInterval(updateAlertStats, 60000);
            
            // 如果当前在抓拍页面，每30秒刷新一次抓拍历史
            setInterval(() => {
                if (document.getElementById('capture-page').style.display !== 'none') {
                    loadCaptureHistory();
                }
            }, 30000);
            
            // 添加触摸反馈
            document.querySelectorAll('.touch-feedback').forEach(element => {
                element.addEventListener('touchstart', function() {
                    this.style.opacity = '0.7';
                });
                element.addEventListener('touchend', function() {
                    this.style.opacity = '1';
                });
            });
        });
    </script>
</body>
</html>
"""
    
    # 创建HTML文件
    html_path = os.path.join(templates_dir, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(HTML_TEMPLATE)
    
    # 打印调试信息
    print("=" * 50)
    print("服务器调试信息:")
    print(f"工作目录: {os.getcwd()}")
    print(f"模板目录: {templates_dir}")
    print(f"静态目录: {app.static_folder}")
    print(f"图片目录: {config.CAPTURED_IMAGE_DIR}")
    print(f"图片目录是否存在: {os.path.exists(config.CAPTURED_IMAGE_DIR)}")
    
    # 列出图片目录中的文件
    if os.path.exists(config.CAPTURED_IMAGE_DIR):
        images = os.listdir(config.CAPTURED_IMAGE_DIR)
        print(f"图片目录中的文件 ({len(images)} 个):")
        for img in images[:5]:  # 只显示前5个
            print(f"  - {img}")
        if len(images) > 5:
            print(f"  ... 还有 {len(images)-5} 个文件")
    else:
        print("图片目录不存在!")
    
    print("=" * 50)
    print("已注册的路由:")
    for rule in app.url_map.iter_rules():
        print(f"  {rule.rule}")
    print("=" * 50)
    
    # 启动Flask应用
    print(f"\n启动移动端Web服务器: http://{config.HOST}:{config.PORT}")
    print(f"请在手机浏览器中访问以上地址")
    
    # 获取本机IP地址
    try:
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        print(f"本地网络访问地址: http://{local_ip}:{config.PORT}")
    except:
        pass
    
    print(f"\n调试URL:")
    print(f"  - 路由列表: http://{local_ip}:{config.PORT}/routes")
    print(f"  - 静态文件调试: http://{local_ip}:{config.PORT}/static/debug")
    print(f"  - 图片目录调试: http://{local_ip}:{config.PORT}/debug/images")
    print(f"  - 测试图片: http://{local_ip}:{config.PORT}/test/image/test.jpg")
    print("=" * 50)
    
    # 使用Flask的开发服务器
    socketio.run(app, host=config.HOST, port=config.PORT, debug=config.DEBUG, allow_unsafe_werkzeug=True)

# ==================== 主程序入口 ====================
if __name__ == "__main__":
    try:
        start_server()
    except KeyboardInterrupt:
        print("\n正在关闭服务器...")
        if mqtt_manager:
            mqtt_manager.disconnect()
    except Exception as e:
        print(f"服务器启动失败: {e}")
        traceback.print_exc()