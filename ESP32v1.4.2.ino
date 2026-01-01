/******************************************************
 * 基于ESP32的车载儿童生命体征监测及降窗系统
 * 主控制器代码 - 整合所有传感器、执行机构和声光报警
 * 
 * 作者：方钦炯
 * 日期：2025年12月1日
 * 
 * 修改说明：
 * 1. 移除了重复的风险评估逻辑，避免与Python端冲突
 * 2. 仅保留车门状态处理和执行Python命令的功能
 * 3. Arduino作为执行单元，不再主动触发紧急响应
 * 
 * 硬件连线：
 * 1. HC-SR501（人体红外传感器）: OUT → ESP32 GPIO13
 * 2. ENS160 + AHT21（空气质量+温湿度）: SDA→GPIO21, SCL→GPIO22
 * 3. AIR780E（4G模块）: TX→GPIO16(U2_RX), RX→GPIO17(U2_TX)
 * 4. GY-906（红外温度传感器）: SDA→GPIO21, SCL→GPIO22 (共享I2C)
 * 5. 高电平触发蜂鸣器: 正极 → ESP32 GPIO27
 * 6. A3144（霍尔传感器）: D0 → ESP32 GPIO12
 * 7. ULN2003 #1（电机1-降窗1）: IN1=19, IN2=23, IN3=25, IN4=26
 * 8. ULN2003 #2（电机2-降窗2）: IN1=15, IN2=18, IN3=5, IN4=32
 * 9. LED指示灯:
 *    - GPIO14: 红+黄并联 → 紧急报警灯
 *    - GPIO33: 蓝+绿并联 → 状态指示灯
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <DFRobot_ENS160.h>
#include <PTSolns_AHTx.h>
#include <Adafruit_MLX90614.h>
#include <HardwareSerial.h>
#include "soc/rtc_cntl_reg.h"  // 添加RTC控制寄存器头文件
#include <ArduinoJson.h>  // 新增：用于解析控制命令的JSON

// ==================== WiFi and MQTT 配置 ====================
const char* ssid = "Mi 11";
const char* password = "25809000";
//const char* mqtt_server = "509pk6184bc5.vicp.fun";22.tcp.cpolar.top sj.frp.one
const char* mqtt_server = "broker.emqx.io";
const int mqtt_port = 1883;
const char* mqtt_topic_child = "esp32cam/child_detection";
const char* mqtt_topic_status = "esp32/main/status";
const char* mqtt_topic_control = "python/control";  // 修改：匹配Python的发布主题
const char* client_id = "ESP32_MainController";

// ==================== LED指示灯配置 ====================
#define WARN_LED  14   // 红+黄并联：紧急报警爆闪
#define STATUS_LED 33  // 蓝+绿并联：状态指示

// ==================== 传感器引脚定义 ====================
// 1. HC-SR501 人体红外传感器
const int pirPin = 13;          // PIR传感器输出引脚
const int ledPin = 2;           // 内置LED（调试用）

// 2. A3144 霍尔传感器（车门状态检测）
#define HALL_SENSOR_PIN 12      // 霍尔传感器数据引脚
bool doorClosed = false;        // 当前车门状态
bool lastDoorState = false;     // 上一次车门状态
unsigned long lastDebounceTime = 0;
const unsigned long debounceDelay = 50;  // 去抖动延时(毫秒)

// 3. 蜂鸣器控制
const int buzzerPin = 27;       // 有源蜂鸣器控制引脚

// 4. I2C传感器（共享总线）
#define SDA_PIN 21
#define SCL_PIN 22

// ==================== 传感器对象 ====================
// ENS160 + AHT21
DFRobot_ENS160_I2C ENS160(&Wire, 0x53);
PTSolns_AHTx aht;

// MLX90614 红外温度传感器（修复初始化方式）
Adafruit_MLX90614 mlx = Adafruit_MLX90614();

// ==================== 电机控制配置 ====================
// ULN2003 #1（控制电机1：降窗1）
const int motor1Pins[] = {19, 23, 25, 26};
// ULN2003 #2（控制电机2：降窗2）
const int motor2Pins[] = {15, 18, 5, 32};

// 步进电机四相八拍的相序
const int stepSequence[8][4] = {
  {1, 0, 0, 0},  // A相通电
  {1, 1, 0, 0},  // A+B相通电
  {0, 1, 0, 0},  // B相通电
  {0, 1, 1, 0},  // B+C相通电
  {0, 0, 1, 0},  // C相通电
  {0, 0, 1, 1},  // C+D相通电
  {0, 0, 0, 1},  // D相通电
  {1, 0, 0, 1}   // D+A相通电
};

const int stepsPerRevolution = 512;  // 28YBJ-48减速比1:64，四相八拍每圈需512步
const int stepDelay = 5;            // 步进间隔（毫秒）

// ==================== 4G模块配置 ====================
HardwareSerial air780eSerial(2);  // 使用UART2
const int air780eResetPin = 4;    // 复位引脚（可选）
const int air780ePwrPin = 2;      // 电源控制引脚（可选）
const char* PHONE_NUMBER = "+8619209878693"; // 手机号码

// 英文短信内容（ASCII ONLY）
const char* SMS_TEXT_EN =
  "EMERGENCY ALERT\r\n"
  "Child trapped in vehicle\r\n"
  "Please check immediately";

// ==================== 系统状态变量 ====================
// 传感器数据
float temperature = 0.0;      // AHT21 温度
float humidity = 0.0;         // AHT21 湿度
uint8_t aqi = 0;              // ENS160 AQI
uint16_t tvoc = 0;            // ENS160 TVOC
uint16_t eco2 = 0;            // ENS160 eCO2
double ambientTemp = 0.0;     // MLX90614环境温度
double objectTemp = 0.0;      // MLX90614物体温度
bool humanDetected = false;   // 人体检测标志
bool childDetected = false;   // 儿童检测标志（来自MQTT）
float childConfidence = 0.0;  // 儿童检测置信度
int childCount = 0;           // 检测到的儿童数量
int adultCount = 0;           // 检测到的成人数量

// PIR传感器状态
int pirState = LOW;
unsigned long lastMotionTime = 0;
unsigned long motionCount = 0;

// 系统状态
enum SystemState {
  STATE_IDLE,           // 空闲状态
  STATE_MONITORING,     // 监测中
  STATE_WARNING,        // 警告状态
  STATE_EMERGENCY,      // 紧急状态
  STATE_VENTILATING     // 通风中
};

SystemState currentState = STATE_IDLE;
unsigned long lastStateChange = 0;
unsigned long systemStartTime = 0;

// 电机控制状态
bool motor1Active = false;
bool motor2Active = false;
unsigned long lastMotorTime = 0;

// 报警状态
bool alarmActive = false;
unsigned long lastAlarmTime = 0;
const unsigned long alarmDuration = 10000;  // 警报持续时间（毫秒）

// 传感器工作状态标志
bool ahtWorking = true;
bool mlxWorking = true;
bool ens160Working = true;

// ==================== WiFi和MQTT对象 ====================
WiFiClient espClient;
PubSubClient client(espClient);

// ==================== 函数声明 ====================
void setupSensors();
void readSensors();
void processChildDetection(const char* payload, unsigned int length);
void processControlCommand(const char* payload, unsigned int length);
void evaluateRisk();  // 简化版风险评估
void executeEmergencyActions();  // 执行紧急动作（仅由命令触发）
void stopEmergencyActions();  // 停止紧急动作
void controlWindows(bool open, int duration = 5000);
void motorRun(int motorNum, int direction, int steps);
void motorStop(int motorNum);
void activateBuzzer(int mode, int duration = 2000);
void activateVisualAlarm(bool state);
void sendSystemStatus();
void setup4GModule();
void sendSMSAlert();  // 修改为发送英文短信
void resetSystem();
void sendSerialSensorData();
void closeWindowsEmergency();  // 新增：紧急关窗函数

// ==================== LED状态函数 ====================
void systemOK() {
  Serial.println("✅ 系统上电自检完成");
  digitalWrite(STATUS_LED, HIGH); 
  delay(1000); 
  digitalWrite(STATUS_LED, LOW);
}

void alarmFlash() {
  Serial.println("🚨 激活红黄警灯爆闪");
  alarmActive = true;
  lastAlarmTime = millis();
  
  // 使用非阻塞方式实现爆闪
  unsigned long startTime = millis();
  while (millis() - startTime < 2000) {  // 持续2秒
    digitalWrite(WARN_LED, HIGH);
    delay(100);
    digitalWrite(WARN_LED, LOW);
    delay(100);
  }
}

void actionDone() {
  Serial.println("✅ 执行动作完成（已降窗+已发短信）");
  digitalWrite(STATUS_LED, HIGH);  // 绿灯常亮表示已处理
  digitalWrite(WARN_LED, LOW);     // 关闭警灯
}

void allClear() {
  Serial.println("✅ 系统恢复正常");
  digitalWrite(WARN_LED, LOW);     // 关闭警灯
  digitalWrite(STATUS_LED, HIGH);  // 蓝灯常亮
  delay(3000);                     // 保持3秒
  digitalWrite(STATUS_LED, LOW);   // 熄灭
  alarmActive = false;
}

// ==================== 设置函数 ====================
void setup() {
  Serial.begin(115200);
  Serial.println("\n\n=== 车载儿童生命体征监测及降窗系统启动 ===");
  Serial.println("=============================================");
  
  // 初始化系统时间
  systemStartTime = millis();
  
  // 关闭棕色断电警告（提高稳定性）- 修复寄存器定义
  REG_SET_BIT(RTC_CNTL_BROWN_OUT_REG, RTC_CNTL_BROWN_OUT_ENA); // 先确保使能
  REG_CLR_BIT(RTC_CNTL_BROWN_OUT_REG, RTC_CNTL_BROWN_OUT_ENA); // 然后清除使能
  
  // 初始化LED指示灯
  pinMode(WARN_LED, OUTPUT);
  pinMode(STATUS_LED, OUTPUT);
  digitalWrite(WARN_LED, LOW);
  digitalWrite(STATUS_LED, LOW);
  Serial.println("LED指示灯初始化完成");
  
  // 执行系统上电自检
  systemOK();
  
  // 初始化引脚
  pinMode(pirPin, INPUT);
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, LOW);
  
  pinMode(buzzerPin, OUTPUT);
  digitalWrite(buzzerPin, LOW);
  
  // 初始化霍尔传感器引脚
  pinMode(HALL_SENSOR_PIN, INPUT);
  doorClosed = (digitalRead(HALL_SENSOR_PIN) == LOW);
  lastDoorState = doorClosed;
  
  // 初始化电机控制引脚
  for (int i = 0; i < 4; i++) {
    pinMode(motor1Pins[i], OUTPUT);
    pinMode(motor2Pins[i], OUTPUT);
    digitalWrite(motor1Pins[i], LOW);
    digitalWrite(motor2Pins[i], LOW);
  }
  
  // 初始化4G模块引脚
  pinMode(air780eResetPin, OUTPUT);
  pinMode(air780ePwrPin, OUTPUT);
  digitalWrite(air780eResetPin, HIGH);  // 正常工作状态
  digitalWrite(air780ePwrPin, HIGH);    // 电源开启
  
  // 按照指定顺序初始化I2C总线 - 关键修复1
  Wire.begin(SDA_PIN, SCL_PIN, 100000);  // SDA=21, SCL=22, 100kHz
  Serial.println("I2C总线初始化成功 (100kHz)");
  
  // 连接WiFi
  WiFi.begin(ssid, password);
  WiFi.setSleep(false);  // 禁用WiFi睡眠模式提高稳定性
  
  Serial.print("正在连接WiFi");
  int wifi_timeout = 30;  // 30秒超时
  while (WiFi.status() != WL_CONNECTED && wifi_timeout > 0) {
    delay(500);
    Serial.print(".");
    wifi_timeout--;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi已连接！");
    Serial.print("IP地址: ");
    Serial.println(WiFi.localIP());
    
    // 设置MQTT服务器和回调函数
    client.setServer(mqtt_server, mqtt_port);
    client.setCallback([](char* topic, byte* payload, unsigned int length) {
      Serial.print("收到MQTT消息 [");
      Serial.print(topic);
      Serial.print("] ");
      
      // 处理儿童检测消息
      if (strcmp(topic, mqtt_topic_child) == 0) {
        processChildDetection((const char*)payload, length);
      }
      // 新增：处理控制命令
      else if (strcmp(topic, mqtt_topic_control) == 0) {
        processControlCommand((const char*)payload, length);
      }
    });
    
    // 初始化传感器 - 关键修复2：按照正确顺序初始化
    setupSensors();
    
    // 初始化4G模块
    setup4GModule();
    
    currentState = STATE_MONITORING;
    Serial.println("系统进入监测状态");
  } else {
    Serial.println("\nWiFi连接失败，进入离线模式");
    currentState = STATE_IDLE;
  }
  
  Serial.println("=============================================");
  Serial.println("系统初始化完成，开始运行...");
}

// ==================== 主循环 ====================
void loop() {
  // 保持MQTT连接
  if (WiFi.status() == WL_CONNECTED) {
    if (!client.connected()) {
      // 重新连接MQTT
      while (!client.connected()) {
        Serial.print("尝试MQTT连接...");
        if (client.connect(client_id)) {
          Serial.println("连接成功");
          client.subscribe(mqtt_topic_child);
          client.subscribe(mqtt_topic_control);  // 新增：订阅控制主题
        } else {
          Serial.print("失败, rc=");
          Serial.print(client.state());
          Serial.println(" 5秒后重试");
          delay(5000);
        }
      }
    }
    client.loop();
  }
  
  // 读取传感器数据
  readSensors();
  
  // 简化版风险评估（仅处理车门状态）
  evaluateRisk();
  
  // 更新霍尔传感器状态（车门检测）
  bool currentReading = (digitalRead(HALL_SENSOR_PIN) == LOW);
  if (currentReading != lastDoorState) {
    lastDebounceTime = millis();
  }
  
  if ((millis() - lastDebounceTime) > debounceDelay) {
    if (currentReading != doorClosed) {
      doorClosed = currentReading;
      Serial.print("[车门状态] ");
      Serial.println(doorClosed ? "车门已关闭" : "车门已打开");
      
      // 车门状态变化时重置系统
      if (doorClosed && (currentState == STATE_EMERGENCY || currentState == STATE_VENTILATING)) {
        Serial.println("车门关闭，重置紧急状态");
        stopEmergencyActions();
        currentState = STATE_MONITORING;
      } else if (!doorClosed && (currentState == STATE_EMERGENCY || currentState == STATE_VENTILATING)) {
        Serial.println("车门打开，系统恢复正常");
        allClear();
        currentState = STATE_MONITORING;
      }
    }
  }
  lastDoorState = currentReading;
  
  // PIR传感器处理
  int pirValue = digitalRead(pirPin);
  if (pirValue == HIGH && pirState == LOW) {
    motionCount++;
    lastMotionTime = millis();
    pirState = HIGH;
    Serial.printf("[PIR] 检测到运动 #%d - 当前状态: ", motionCount);
    Serial.println(humanDetected ? "已确认人体" : "待确认");
  } else if (pirValue == LOW && pirState == HIGH) {
    pirState = LOW;
    Serial.println("[PIR] 运动结束");
  }
  
  // 定期发送系统状态
  static unsigned long lastStatusTime = 0;
  if (millis() - lastStatusTime > 5000) {  // 每5秒发送一次状态
    sendSystemStatus();
    lastStatusTime = millis();
  }
  
  // 监控WiFi连接状态
  static unsigned long lastWiFiCheck = 0;
  if (millis() - lastWiFiCheck > 10000) {  // 每10秒检查一次
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("WiFi连接丢失，尝试重新连接...");
      WiFi.reconnect();
    }
    lastWiFiCheck = millis();
  }
  
  // 持续警报处理
  if (alarmActive && (millis() - lastAlarmTime < alarmDuration)) {
    // 继续警报（蜂鸣器+LED）
    if ((millis() - lastAlarmTime) % 500 < 250) {
      digitalWrite(WARN_LED, HIGH);
    } else {
      digitalWrite(WARN_LED, LOW);
    }
  } else if (alarmActive) {
    // 警报结束
    digitalWrite(WARN_LED, LOW);
    digitalWrite(buzzerPin, LOW);
    alarmActive = false;
  }
  
  // 发送串口传感器数据
  sendSerialSensorData();
  
  delay(100);  // 短暂延迟以节省CPU
}

// ==================== 处理控制命令 ====================
void processControlCommand(const char* payload, unsigned int length) {
  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, payload, length);

  if (error) {
    Serial.print("JSON解析失败: ");
    Serial.println(error.c_str());
    return;
  }

  const char* command = doc["command"];
  if (strcmp(command, "lower_window") == 0) {
    int percent = doc["params"]["percent"] | 100;  // 默认100%
    int duration = map(percent, 0, 100, 0, 5000);  // 映射到持续时间
    Serial.println("收到降窗命令");
    controlWindows(true, duration);
  } else if (strcmp(command, "close_window") == 0) {  // 新增：关窗命令
    int percent = doc["params"]["percent"] | 100;     // 默认100%
    int duration = map(percent, 0, 100, 0, 5000);     // 映射到持续时间
    Serial.println("收到关窗命令");
    controlWindows(false, duration);
  } else if (strcmp(command, "test_alarm") == 0) {
    Serial.println("收到测试报警命令");
    alarmFlash();  // 视觉报警
    activateBuzzer(3, 2000);  // 蜂鸣器警报模式，持续2秒
  } else if (strcmp(command, "send_sms") == 0) {  // 新增：处理send_sms命令
    Serial.println("收到发送短信命令");
    sendSMSAlert();  // 调用发送短信函数
  } else if (strcmp(command, "emergency_response") == 0) {  // 新增：紧急响应命令
    Serial.println("收到紧急响应命令");
    executeEmergencyActions();  // 执行完整的紧急响应
  } else if (strcmp(command, "reset_system") == 0) {  // 新增：系统复位命令
    Serial.println("收到系统复位命令");
    resetSystem();  // 重置系统状态
  } else {
    Serial.println("未知命令");
  }
}

// ==================== 传感器设置函数（修复初始化顺序） ====================
void setupSensors() {
  Serial.println("\n=== 按指定顺序初始化传感器 ===");
  Serial.println("顺序: 1.MLX90614 → 2.AHT21 → 3.ENS160");
  
  // 关键修复3：严格按照指定顺序初始化
  
  // 1. 首先初始化MLX90614红外温度传感器
  Serial.print("1. 初始化MLX90614红外温度传感器...");
  mlxWorking = false;
  for (int retry = 0; retry < 3; retry++) {
    if (mlx.begin(0x5A)) {
      mlxWorking = true;
      Serial.println("✅ 成功 (地址: 0x5A)");
      break;
    }
    Serial.print(".");
    delay(500);
  }
  if (!mlxWorking) {
    Serial.println("❌ 失败！MLX90614未连接或地址冲突");
  }

  // 2. 然后初始化AHT21温湿度传感器
  Serial.print("2. 初始化AHT21温湿度传感器...");
  ahtWorking = false;
  for (int retry = 0; retry < 3; retry++) {
    // 先尝试标准地址0x38
    if (aht.begin(Wire, 0x38)) {
      AHTxStatus status = aht.readTemperatureHumidity(temperature, humidity, 120);
      if (status == AHTX_OK) {
        ahtWorking = true;
        Serial.println("✅ 成功 (地址: 0x38)");
        break;
      }
    }
    
    // 尝试备用地址0x5C
    if (aht.begin(Wire, 0x5C)) {
      AHTxStatus status = aht.readTemperatureHumidity(temperature, humidity, 120);
      if (status == AHTX_OK) {
        ahtWorking = true;
        Serial.println("✅ 成功 (备用地址: 0x5C)");
        break;
      }
    }
    
    Serial.print(".");
    delay(500);
  }
  if (!ahtWorking) {
    Serial.println("❌ 失败！请检查AHT21连接");
  }

  // 3. 最后初始化ENS160空气质量传感器
  Serial.print("3. 初始化ENS160空气质量传感器...");
  ens160Working = false;
  for (int retry = 0; retry < 3; retry++) {
    if (ENS160.begin() == NO_ERR) {
      ENS160.setPWRMode(ENS160_STANDARD_MODE);
      ens160Working = true;
      Serial.println("✅ 成功");
      
      // 设置温湿度补偿（如果AHT21工作正常）
      if (ahtWorking) {
        ENS160.setTempAndHum(temperature, humidity);
        Serial.println("   → 已设置温湿度补偿");
      }
      break;
    }
    Serial.print(".");
    delay(1000);
  }
  if (!ens160Working) {
    Serial.println("❌ 失败！ENS160未连接");
  }

  // 最终状态总结
  Serial.println("\n=== 传感器初始化结果 ===");
  Serial.printf("MLX90614: %s\n", mlxWorking ? "✅ 正常工作" : "❌ 未工作");
  Serial.printf("AHT21:    %s\n", ahtWorking ? "✅ 正常工作" : "❌ 未工作");
  Serial.printf("ENS160:   %s\n", ens160Working ? "✅ 正常工作" : "❌ 未工作");
  
  if (mlxWorking && ahtWorking && ens160Working) {
    Serial.println("✅ 所有传感器初始化成功，系统正常运行");
  } else {
    Serial.println("⚠️ 部分传感器未工作，系统将降级运行");
  }
  Serial.println("==========================");
}

// ==================== 传感器读取函数（增强容错版） ====================
void readSensors() {
  static unsigned long lastAHTRead = 0;
  static unsigned long lastMLXRead = 0;
  static unsigned long lastENS160Read = 0;
  
  // 读取MLX90614红外温度
  if (mlxWorking && (millis() - lastMLXRead > 1000)) {
    ambientTemp = mlx.readAmbientTempC();
    objectTemp = mlx.readObjectTempC();
    
    // 人体检测逻辑（温度范围30-42°C）
    humanDetected = (objectTemp >= 30.0 && objectTemp <= 42.0);
    
    // 检查传感器是否失效
    if (isnan(ambientTemp) || isnan(objectTemp)) {
      Serial.println("⚠️ MLX90614读取无效，尝试重新初始化");
      mlxWorking = mlx.begin(0x5A);
      if (!mlxWorking) {
        Serial.println("❌ MLX90614重新初始化失败");
      }
    }
    lastMLXRead = millis();
  }
  
  // 读取AHT21温湿度
  if (ahtWorking && (millis() - lastAHTRead > 2000)) {
    AHTxStatus aht_status = aht.readTemperatureHumidity(temperature, humidity, 120);
    if (aht_status != AHTX_OK) {
      Serial.printf("⚠️ AHT21读取失败，错误码: %d\n", (int)aht_status);
      
      // 尝试重新初始化
      if (aht.begin(Wire, 0x38) || aht.begin(Wire, 0x5C)) {
        Serial.println("🔄 尝试重新读取AHT21");
        aht_status = aht.readTemperatureHumidity(temperature, humidity, 120);
        if (aht_status != AHTX_OK) {
          ahtWorking = false;
          Serial.println("❌ AHT21持续失效，暂时禁用");
        }
      } else {
        ahtWorking = false;
        Serial.println("❌ AHT21重新初始化失败，暂时禁用");
      }
    }
    lastAHTRead = millis();
  } else if (!ahtWorking) {
    // 如果AHT21无法工作，使用默认值或标记为无效
    temperature = -999.0; // 无效值标记
    humidity = -999.0;
    
    // 每30秒尝试恢复
    static unsigned long lastRecoveryAttempt = 0;
    if (millis() - lastRecoveryAttempt > 30000) {
      Serial.println("🔄 尝试恢复AHT21传感器...");
      if (aht.begin(Wire, 0x38) || aht.begin(Wire, 0x5C)) {
        AHTxStatus aht_status = aht.readTemperatureHumidity(temperature, humidity, 120);
        if (aht_status == AHTX_OK) {
          ahtWorking = true;
          Serial.println("✅ AHT21传感器恢复成功");
        } else {
          Serial.println("❌ 恢复失败，继续禁用");
        }
      }
      lastRecoveryAttempt = millis();
    }
  }
  
  // 读取ENS160空气质量数据
  if (ens160Working && (millis() - lastENS160Read > 3000)) {
    // 仅当AHT21工作正常时才更新温湿度补偿
    if (ahtWorking && temperature > -100 && humidity > -100) {
      ENS160.setTempAndHum(temperature, humidity);
    }
    
    aqi = ENS160.getAQI();
    tvoc = ENS160.getTVOC();
    eco2 = ENS160.getECO2();
    
    // 检查数据有效性
    if (aqi == 0 && tvoc == 0 && eco2 == 0) {
      Serial.println("⚠️ ENS160数据异常，尝试重新初始化");
      if (ENS160.begin() == NO_ERR) {
        ENS160.setPWRMode(ENS160_STANDARD_MODE);
        Serial.println("✅ ENS160重新初始化成功");
      } else {
        ens160Working = false;
        Serial.println("❌ ENS160重新初始化失败，禁用该传感器");
      }
    }
    lastENS160Read = millis();
  }
  
  // 打印传感器数据（每5秒）
  static unsigned long lastPrint = 0;
  if (millis() - lastPrint > 5000) {
    Serial.println("\n=== 传感器数据 ===");
    
    // MLX90614数据
    if (mlxWorking) {
      Serial.printf("【红外】环境温度: %.1f°C, 物体温度: %.1f°C → %s\n", 
                   ambientTemp, objectTemp, humanDetected ? "检测到人体" : "未检测到人体");
    } else {
      Serial.println("【红外】MLX90614未工作，无法提供温度数据");
    }
    
    // AHT21数据
    if (ahtWorking) {
      Serial.printf("【环境】温度: %.1f°C, 湿度: %.1f%%\n", temperature, humidity);
    } else {
      Serial.println("【环境】AHT21未工作，无法提供温湿度数据");
    }
    
    // ENS160数据
    if (ens160Working) {
      Serial.printf("【空气】AQI: %d (%s), TVOC: %dppb, eCO2: %dppm\n", 
                    aqi, (aqi == 1) ? "优秀" : (aqi == 2) ? "良好" : (aqi == 3) ? "中等" : (aqi == 4) ? "较差" : "恶劣",
                    tvoc, eco2);
    } else {
      Serial.println("【空气】ENS160未工作，无法提供空气质量数据");
    }
    
    // 系统状态
    Serial.printf("【车门】%s\n", doorClosed ? "已关闭" : "已打开");
    Serial.printf("【PIR】%s, 总检测次数: %lu\n", pirState == HIGH ? "运动中" : "静止", motionCount);
    Serial.printf("【视觉】儿童: %s (置信度: %.2f), 数量: %d, 成人: %d\n", 
                  childDetected ? "是" : "否", childConfidence, childCount, adultCount);
    Serial.println("==================");
    
    lastPrint = millis();
  }
}

// ==================== MQTT消息处理 ====================
void processChildDetection(const char* payload, unsigned int length) {
  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  
  // 解析JSON消息
  if (message.indexOf("child_detected") != -1) {
    if (message.indexOf("\"child_detected\":true") != -1) {
      childDetected = true;
      
      // 提取置信度
      int confStart = message.indexOf("\"confidence\":");
      if (confStart != -1) {
        int confEnd = message.indexOf(",", confStart);
        if (confEnd == -1) confEnd = message.indexOf("}", confStart);
        if (confEnd != -1) {
          String confStr = message.substring(confStart + 13, confEnd);
          childConfidence = confStr.toFloat();
        }
      }
      
      // 提取儿童数量
      int childCountStart = message.indexOf("\"child_count\":");
      if (childCountStart != -1) {
        int childCountEnd = message.indexOf(",", childCountStart);
        if (childCountEnd == -1) childCountEnd = message.indexOf("}", childCountStart);
        if (childCountEnd != -1) {
          String countStr = message.substring(childCountStart + 14, childCountEnd);
          childCount = countStr.toInt();
        }
      }
      
      // 提取成人数量
      int adultCountStart = message.indexOf("\"adult_count\":");
      if (adultCountStart != -1) {
        int adultCountEnd = message.indexOf(",", adultCountStart);
        if (adultCountEnd == -1) adultCountEnd = message.indexOf("}", adultCountStart);
        if (adultCountEnd != -1) {
          String countStr = message.substring(adultCountStart + 14, adultCountEnd);
          adultCount = countStr.toInt();
        }
      }
      
      Serial.printf("[视觉] 检测到儿童！置信度: %.2f, 儿童数: %d, 成人数: %d\n", 
                   childConfidence, childCount, adultCount);
    } else {
      childDetected = false;
      childConfidence = 0.0;
      childCount = 0;
      adultCount = 0;
    }
  }
}

// ==================== 风险评估函数（简化版） ====================
void evaluateRisk() {
  // 简化为只处理车门状态变化时的动作
  // 移除原有的复杂风险评估逻辑，避免与Python端冲突
  
  // 如果车门打开且之前处于紧急状态，则恢复正常
  if (!doorClosed && (currentState == STATE_EMERGENCY || currentState == STATE_VENTILATING)) {
    Serial.println("\n✅ 车门已打开，风险解除，正在关闭车窗...");
    
    // 停止所有警报
    digitalWrite(buzzerPin, LOW);
    digitalWrite(WARN_LED, LOW);
    
    // 自动关窗
    closeWindowsEmergency();
    
    // 恢复系统状态
    currentState = STATE_MONITORING;
    
    Serial.println("✅ 系统已恢复正常监测状态");
  }
  
  // 注意：原有的风险评估逻辑已移除，决策完全由Python端控制
}

// ==================== 紧急响应函数（仅由命令触发） ====================
void executeEmergencyActions() {
  // 只有在收到Python端的紧急响应命令时才会执行
  currentState = STATE_EMERGENCY;
  
  // 1. 激活声光报警（红黄爆闪 + 蜂鸣器）
  Serial.println("🚨🚨🚨 启动声光报警系统 🚨🚨🚨");
  alarmFlash();  // 视觉报警
  activateBuzzer(3, 10000);  // 警报模式，持续10秒
  
  // 2. 通过4G模块发送英文短信报警
  Serial.println("正在通过4G模块发送报警短信...");
  sendSMSAlert();  // 发送英文短信
  
  // 3. 启动降窗通风（5秒）
  Serial.println("启动降窗通风...");
  controlWindows(true, 5000);
  
  currentState = STATE_VENTILATING;
  
  // 4. 标记动作完成
  actionDone();
}

void stopEmergencyActions() {
  // 停止蜂鸣器
  digitalWrite(buzzerPin, LOW);
  
  // 停止电机
  motorStop(1);
  motorStop(2);
  motor1Active = false;
  motor2Active = false;
  
  // 自动关窗（仅在当前窗户是打开状态时）
  Serial.println("风险解除，自动关闭车窗...");
  closeWindowsEmergency();
  
  // 重置儿童检测状态
  childDetected = false;
  childConfidence = 0.0;
  childCount = 0;
  adultCount = 0;
  
  // 重置PIR计数
  motionCount = 0;
  
  // 重置报警状态
  alarmActive = false;
  digitalWrite(WARN_LED, LOW);
  digitalWrite(buzzerPin, LOW);
  
  // 恢复正常状态
  allClear();
}

// ==================== 电机控制函数 ====================
void controlWindows(bool open, int duration) {
  if (open) {
    Serial.println("🚗🚗🚗 正在降窗通风... 🚗🚗🚗");
    
    // 启动两个电机（降窗 = 反转 = 方向0）
    motor1Active = true;
    motor2Active = true;
    
    // 创建电机控制任务（非阻塞式）
    xTaskCreatePinnedToCore(
      [](void* param) {
        int steps = (stepsPerRevolution * 2) / 3;  // 降窗2/3圈
        motorRun(1, 0, steps);  // 电机1反转
        vTaskDelete(NULL);
      },
      "Motor1OpenTask", 4096, NULL, 1, NULL, 0
    );
    
    xTaskCreatePinnedToCore(
      [](void* param) {
        int steps = (stepsPerRevolution * 2) / 3;  // 降窗2/3圈
        motorRun(2, 0, steps);  // 电机2反转
        vTaskDelete(NULL);
      },
      "Motor2OpenTask", 4096, NULL, 1, NULL, 0
    );
    
    lastMotorTime = millis();
  } else {
    Serial.println("🚗🚗🚗 正在关窗... 🚗🚗🚗");
    
    // 启动两个电机（关窗 = 正转 = 方向1）
    motor1Active = true;
    motor2Active = true;
    
    // 创建电机控制任务（非阻塞式）
    xTaskCreatePinnedToCore(
      [](void* param) {
        int steps = (stepsPerRevolution * 2) / 3;  // 关窗2/3圈
        motorRun(1, 1, steps);  // 电机1正转
        vTaskDelete(NULL);
      },
      "Motor1CloseTask", 4096, NULL, 1, NULL, 0
    );
    
    xTaskCreatePinnedToCore(
      [](void* param) {
        int steps = (stepsPerRevolution * 2) / 3;  // 关窗2/3圈
        motorRun(2, 1, steps);  // 电机2正转
        vTaskDelete(NULL);
      },
      "Motor2CloseTask", 4096, NULL, 1, NULL, 0
    );
    
    lastMotorTime = millis();
  }
}

// ==================== 新增：紧急关窗函数 ====================
void closeWindowsEmergency() {
  Serial.println("🚨🚨🚨 紧急风险解除，正在关闭车窗 🚨🚨🚨");
  
  // 停止任何正在运行的电机
  motorStop(1);
  motorStop(2);
  
  // 等待一小段时间
  delay(100);
  
  // 关窗（正转）
  controlWindows(false, 5000);
  
  // 等待关窗完成
  delay(6000);  // 比运行时间稍长一点
  
  Serial.println("✅ 车窗已关闭");
}

void motorRun(int motorNum, int direction, int steps) {
  int* currentMotorPins;
  
  if (motorNum == 1) {
    currentMotorPins = (int*)motor1Pins;
  } else if (motorNum == 2) {
    currentMotorPins = (int*)motor2Pins;
  } else {
    Serial.println("错误：电机编号仅支持1或2！");
    return;
  }
  
  Serial.printf("电机%d %s 运行 %d 步\n", motorNum, direction ? "正转" : "反转", steps);
  
  for (int step = 0; step < steps; step++) {
    int sequenceIndex;
    if (direction == 1) {
      sequenceIndex = step % 8;  // 正转：循环取0-7
    } else {
      sequenceIndex = 7 - (step % 8);  // 反转：循环取7-0
    }
    
    for (int pin = 0; pin < 4; pin++) {
      digitalWrite(currentMotorPins[pin], stepSequence[sequenceIndex][pin]);
    }
    delay(stepDelay);
  }
  
  // 电机停止
  for (int pin = 0; pin < 4; pin++) {
    digitalWrite(currentMotorPins[pin], LOW);
  }
  
  Serial.printf("电机%d 运行完成\n", motorNum);
  
  // 更新电机状态标志
  if (motorNum == 1) {
    motor1Active = false;
  } else {
    motor2Active = false;
  }
}

void motorStop(int motorNum) {
  int* currentMotorPins;
  
  if (motorNum == 1) {
    currentMotorPins = (int*)motor1Pins;
  } else if (motorNum == 2) {
    currentMotorPins = (int*)motor2Pins;
  } else {
    return;
  }
  
  for (int pin = 0; pin < 4; pin++) {
    digitalWrite(currentMotorPins[pin], LOW);
  }
  
  // 更新电机状态标志
  if (motorNum == 1) {
    motor1Active = false;
  } else {
    motor2Active = false;
  }
}

// ==================== 蜂鸣器控制函数 ====================
void activateBuzzer(int mode, int duration) {
  // mode: 1=单次, 2=双次, 3=警报, 4=长鸣
  unsigned long startTime = millis();
  unsigned long lastBeep = 0;
  
  Serial.println("🔔 激活蜂鸣器警报");
  
  while (millis() - startTime < (unsigned long)duration) {
    switch (mode) {
      case 1:  // 单次蜂鸣
        if (millis() - lastBeep > 1000) {
          digitalWrite(buzzerPin, HIGH);
          delay(200);
          digitalWrite(buzzerPin, LOW);
          lastBeep = millis();
        }
        break;
        
      case 2:  // 双次蜂鸣
        if (millis() - lastBeep > 500) {
          digitalWrite(buzzerPin, HIGH);
          delay(100);
          digitalWrite(buzzerPin, LOW);
          delay(100);
          digitalWrite(buzzerPin, HIGH);
          delay(100);
          digitalWrite(buzzerPin, LOW);
          lastBeep = millis();
        }
        break;
        
      case 3:  // 警报模式
        if (millis() - lastBeep > 200) {
          digitalWrite(buzzerPin, !digitalRead(buzzerPin));
          lastBeep = millis();
        }
        break;
        
      case 4:  // 长鸣
        digitalWrite(buzzerPin, HIGH);
        break;
    }
    
    delay(10);
  }
  
  // 确保蜂鸣器关闭
  digitalWrite(buzzerPin, LOW);
  Serial.println("🔔 蜂鸣器警报结束");
}

// ==================== 系统状态上报 ====================
void sendSystemStatus() {
  if (!client.connected()) return;
  
  // 修复字符串格式错误
  char statusMsg[256];
  snprintf(statusMsg, sizeof(statusMsg), 
            "{\"timestamp\":%lu,\"state\":\"%s\",\"temperature\":%.1f,\"humidity\":%.1f,\"aqi\":%d,"
         "\"tvoc\":%d,\"eco2\":%d,\"object_temp\":%.1f,\"human_detected\":%s,"
         "\"child_detected\":%s,\"child_confidence\":%.2f,\"door_closed\":%s,"
         "\"sensors\":{\"mlx\":%s,\"aht\":%s,\"ens\":%s}}",
           millis() / 1000,
           (currentState == STATE_IDLE) ? "IDLE" :
           (currentState == STATE_MONITORING) ? "MONITORING" :
           (currentState == STATE_WARNING) ? "WARNING" :
           (currentState == STATE_EMERGENCY) ? "EMERGENCY" : "VENTILATING",
           ahtWorking ? temperature : -999.0,
           ahtWorking ? humidity : -999.0,
           ens160Working ? aqi : 0,
           ens160Working ? tvoc : 0,
           ens160Working ? eco2 : 0,
           mlxWorking ? objectTemp : -999.0,
           humanDetected ? "true" : "false",
           childDetected ? "true" : "false", childConfidence,
           doorClosed ? "true" : "false",
           mlxWorking ? "true" : "false",
           ahtWorking ? "true" : "false",
           ens160Working ? "true" : "false");
  
  client.publish(mqtt_topic_status, statusMsg);
  
  // 调试输出
  static unsigned long lastDebugPrint = 0;
  if (millis() - lastDebugPrint > 10000) {
    Serial.printf("[状态上报] MQTT消息: %s\n", statusMsg);
    lastDebugPrint = millis();
  }
}

// ==================== 4G模块初始化 ====================
void setup4GModule() {
  Serial.println("\n=== 初始化4G模块(AIR780E) ===");
  
  // 初始化串口
  air780eSerial.begin(115200, SERIAL_8N1, 16, 17);  // RX=17, TX=16
  
  // 硬件复位4G模块
  digitalWrite(air780eResetPin, LOW);
  delay(100);
  digitalWrite(air780eResetPin, HIGH);
  delay(2000);
  
  Serial.println("4G模块复位完成");
  
  // 发送AT命令测试
  air780eSerial.println("AT");
  delay(1000);
  
  while (air780eSerial.available()) {
    Serial.write(air780eSerial.read());
  }
  
  Serial.println("4G模块初始化完成");
}

// ==================== 短信报警函数 - 发送英文短信 ====================
void sendSMSAlert() {
  Serial.println("=== SEND ENGLISH SMS START ===");
  Serial.println("📱 正在发送英文短信报警...");

  // 清空串口缓冲区
  while (air780eSerial.available()) {
    air780eSerial.read();
  }

  // 1. 设置短信文本模式
  air780eSerial.println("AT+CMGF=1");
  delay(300);

  // 2. 设置字符集为GSM（适合发送ASCII英文）
  air780eSerial.println("AT+CSCS=\"GSM\"");
  delay(300);

  // 3. 设置短信参数
  air780eSerial.println("AT+CSMP=17,167,0,0");
  delay(300);

  // 4. 进入发送状态
  air780eSerial.print("AT+CMGS=\"");
  air780eSerial.print(PHONE_NUMBER);
  air780eSerial.print("\"\r");   // 必须 \r
  delay(800);              // 等待 '>'

  // 5. 发送英文正文（ASCII）
  air780eSerial.print(SMS_TEXT_EN);

  // 6. 结束符（HEX 1A）
  air780eSerial.write(0x1A);

  Serial.println("=== SMS SENT ===");
  
  // 读取模块响应
  delay(3000);
  Serial.print("模块响应: ");
  while (air780eSerial.available()) {
    Serial.write(air780eSerial.read());
  }
  
  Serial.println("\n📱 英文短信发送流程完成");
}

// ==================== 系统重置函数 ====================
void resetSystem() {
  Serial.println("🔄 重置系统状态");
  
  // 重置传感器状态
  motionCount = 0;
  pirState = LOW;
  childDetected = false;
  humanDetected = false;
  
  // 重置电机状态
  motorStop(1);
  motorStop(2);
  motor1Active = false;
  motor2Active = false;
  
  // 重置系统状态
  currentState = STATE_MONITORING;
  lastStateChange = millis();
  
  // 重置报警状态
  alarmActive = false;
  digitalWrite(WARN_LED, LOW);
  digitalWrite(buzzerPin, LOW);
  
  Serial.println("✅ 系统重置完成");
}

// ==================== 通过串口发送传感器数据 ====================
void sendSerialSensorData() {
  static unsigned long lastSerialSend = 0;
  if (millis() - lastSerialSend > 1000) { // 每秒发送一次
    String jsonData = "{";
    jsonData += "\"source\":\"serial\",";
    jsonData += "\"timestamp\":" + String(millis() / 1000) + ",";
    jsonData += "\"temperature\":" + String(ahtWorking ? temperature : -999.0) + ",";
    jsonData += "\"humidity\":" + String(ahtWorking ? humidity : -999.0) + ",";
    jsonData += "\"aqi\":" + String(ens160Working ? aqi : 0) + ",";
    jsonData += "\"tvoc\":" + String(ens160Working ? tvoc : 0) + ",";
    jsonData += "\"eco2\":" + String(ens160Working ? eco2 : 0) + ",";
    jsonData += "\"object_temp\":" + String(mlxWorking ? objectTemp : -999.0) + ",";
    jsonData += "\"human_detected\":" + String(humanDetected ? "true" : "false") + ",";
    jsonData += "\"child_detected\":" + String(childDetected ? "true" : "false") + ",";
    jsonData += "\"child_confidence\":" + String(childConfidence) + ",";
    jsonData += "\"door_closed\":" + String(doorClosed ? "true" : "false") + ",";
    jsonData += "\"pir_state\":" + String(pirState == HIGH ? "true" : "false") + ",";
    jsonData += "\"state\":\"" + String(
      (currentState == STATE_IDLE) ? "IDLE" :
      (currentState == STATE_MONITORING) ? "MONITORING" :
      (currentState == STATE_WARNING) ? "WARNING" :
      (currentState == STATE_EMERGENCY) ? "EMERGENCY" : "VENTILATING"
    ) + "\"";
    jsonData += "}";
    
    Serial.println(jsonData);
    lastSerialSend = millis();
  }
}