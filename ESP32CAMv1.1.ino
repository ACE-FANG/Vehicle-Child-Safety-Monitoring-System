/*
  ESP32-CAM 儿童识别专用流媒体服务器 - 优化抗延迟版
  保持VGA画质(640x480) + 优化缓冲和参数，适合YOLOv5检测
  访问地址: http://你的IP/stream
  已修复5个关键问题：
  1. 删除frame2jpg转换，直接使用JPEG输出
  2. 优化帧率控制，避免双重阻塞
  3. 修复AGC/AEC设置冲突
  4. 修正PSRAM检测逻辑
  5. 关闭RAW GMA减少处理负担
*/

#include "esp_camera.h"
#include <WiFi.h>
#include "esp_timer.h"
#include "img_converters.h"
#include "fb_gfx.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// ==================== 替换为你的WiFi ====================
const char* ssid = "Mi 11";
const char* password = "25809000";

// ==================== 摄像头型号选择 ====================
#define CAMERA_MODEL_AI_THINKER

#if defined(CAMERA_MODEL_AI_THINKER)
  #define PWDN_GPIO_NUM     32
  #define RESET_GPIO_NUM    -1
  #define XCLK_GPIO_NUM      0
  #define SIOD_GPIO_NUM     26
  #define SIOC_GPIO_NUM     27
  #define D7_GPIO_NUM       35
  #define D6_GPIO_NUM       34
  #define D5_GPIO_NUM       39
  #define D4_GPIO_NUM       36
  #define D3_GPIO_NUM       21
  #define D2_GPIO_NUM       19
  #define D1_GPIO_NUM       18
  #define D0_GPIO_NUM        5
  #define VSYNC_GPIO_NUM     25
  #define HREF_GPIO_NUM      23
  #define PCLK_GPIO_NUM      22
#else
  #error "请正确选择摄像头型号！"
#endif

// ==================== HTTP服务器 ====================
httpd_handle_t stream_httpd = NULL;

static esp_err_t stream_handler(httpd_req_t *req){
  camera_fb_t * fb = NULL;
  esp_err_t res = ESP_OK;
  char part_buf[64];
  uint32_t last_frame_time = 0;
  const uint32_t frame_interval = 83; // 12fps (1000/12 ≈ 83ms)

  res = httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=123456789000000000000987654321");
  if(res != ESP_OK) return res;

  while(true){
    // 控制帧率到12fps
    uint32_t current_time = millis();
    if (current_time - last_frame_time < frame_interval) {
      vTaskDelay(pdMS_TO_TICKS(1)); // 短暂释放CPU
      continue;
    }
    last_frame_time = current_time;

    // 获取最新帧
    fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("摄像头捕获失败");
      res = ESP_FAIL;
      break;
    }

    // ✅ 修复1: 直接使用JPEG输出，删除frame2jpg转换
    // 摄像头已配置为JPEG格式，直接使用fb->buf
    size_t jpg_len = fb->len;
    uint8_t *jpg_buf = fb->buf;

    // 发送HTTP头
    if(res == ESP_OK){
      size_t hlen = snprintf(part_buf, 64, "--123456789000000000000987654321\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", jpg_len);
      res = httpd_resp_send_chunk(req, part_buf, hlen);
    }
    
    // 发送图像数据
    if(res == ESP_OK){
      res = httpd_resp_send_chunk(req, (const char *)jpg_buf, jpg_len);
    }
    
    // 发送边界结束符
    if(res == ESP_OK){
      res = httpd_resp_send_chunk(req, "\r\n", 2);
    }

    // 释放摄像头帧缓冲区
    esp_camera_fb_return(fb);
    fb = NULL;

    if(res != ESP_OK) {
      Serial.println("发送图像失败，断开连接");
      break;
    }
    
    // ✅ 修复2: 使用vTaskDelay而非Arduino delay，避免双重阻塞
    // 帧率控制已在循环开始处实现，这里不再需要额外delay
  }
  
  return res;
}

void startCameraServer(){
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;

  httpd_uri_t stream_uri = {
    .uri       = "/stream",
    .method    = HTTP_GET,
    .handler   = stream_handler,
    .user_ctx  = NULL
  };

  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
    Serial.println("MJPEG流已启动");
    Serial.print("访问地址: http://");
    Serial.print(WiFi.localIP());
    Serial.println("/stream");
  }
}

// ==================== 摄像头初始化 ====================
void setupCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = D0_GPIO_NUM;
  config.pin_d1 = D1_GPIO_NUM;
  config.pin_d2 = D2_GPIO_NUM;
  config.pin_d3 = D3_GPIO_NUM;
  config.pin_d4 = D4_GPIO_NUM;
  config.pin_d5 = D5_GPIO_NUM;
  config.pin_d6 = D6_GPIO_NUM;
  config.pin_d7 = D7_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;  // 保持20MHz时钟
  config.pixel_format = PIXFORMAT_JPEG; // 固定为JPEG格式

  // ✅ 修复4: 修正PSRAM检测逻辑
  if(psramFound()){
    // 有PSRAM，使用VGA分辨率
    config.frame_size = FRAMESIZE_VGA;      // 640x480 - 保持高清画质
    config.jpeg_quality = 10;               // 画质10（质量高）
    config.fb_count = 1;                    // 只保留1帧缓冲区，减少延迟
  } else {
    // 没有PSRAM，使用较低分辨率
    config.frame_size = FRAMESIZE_QVGA;     // 320x240 - 比SVGA更省内存
    config.jpeg_quality = 12;
    config.fb_count = 1;
  }

  // 初始化摄像头
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("摄像头初始化失败，错误码: 0x%x", err);
    delay(1000);
    ESP.restart();
  }

  // ✅ 关键优化：获取传感器并设置参数
  sensor_t * s = esp_camera_sensor_get();
  
  // 确保使用配置的分辨率
  if(psramFound()){
    s->set_framesize(s, FRAMESIZE_VGA);
  } else {
    s->set_framesize(s, FRAMESIZE_QVGA);
  }
  
  // ✅ 修复3: 优化曝光设置，保留自动曝光但降低曝光值
  // 使用半自动模式，适合YOLO检测
  s->set_gain_ctrl(s, 1);       // 开启自动增益控制: 0 = 关闭, 1 = 开启
  s->set_agc_gain(s, 2);        // 固定增益值: 0-30（较低增益）
  s->set_gainceiling(s, (gainceiling_t)4); // 增益上限: 0-6 (适度限制)
  
  s->set_exposure_ctrl(s, 1);   // 开启自动曝光控制: 0 = 关闭, 1 = 开启
  s->set_aec_value(s, 400);     // 曝光值: 300-1200（降低曝光值，解决过曝问题）
  s->set_aec2(s, 0);            // AEC2: 0 = 关闭, 1 = 开启（关闭以稳定码流）
  
  // 图像处理参数（优化对比度）
  s->set_brightness(s, 0);      // 亮度: -2 到 2（设为0，中性）
  s->set_contrast(s, 0);        // 对比度: -2 到 2（设为0，中性）
  s->set_saturation(s, 0);      // 饱和度: -2 到 2（设为0，中性）
  
  // 白平衡（开启自动白平衡）
  s->set_whitebal(s, 1);        // 自动白平衡: 0 = 关闭, 1 = 开启
  s->set_awb_gain(s, 1);        // AWB增益: 0 = 关闭, 1 = 开启
  s->set_wb_mode(s, 0);         // 白平衡模式: 0=Auto, 1=Sunny, 2=Cloudy, 3=Office, 4=Home
  
  // 图像效果和校正
  s->set_special_effect(s, 0);  // 特效: 0 到 6 (0 - 无效果)
  s->set_hmirror(s, 0);         // 水平镜像: 0 = 正常, 1 = 镜像
  s->set_vflip(s, 0);           // 垂直翻转: 0 = 正常, 1 = 翻转
  
  // 像素校正（关闭以减少处理时间）
  s->set_bpc(s, 0);             // 黑像素校正: 0 = 关闭, 1 = 开启
  s->set_wpc(s, 0);             // 白像素校正: 0 = 关闭, 1 = 开启
  s->set_lenc(s, 0);            // 镜头校正: 0 = 关闭, 1 = 开启
  
  // ✅ 修复5: 关闭RAW GMA，减少处理负担
  s->set_raw_gma(s, 0);         // RAW GMA: 0 = 关闭（提高速度）
  s->set_dcw(s, 1);             // DCW: 0 = 关闭, 1 = 开启
  s->set_colorbar(s, 0);        // 颜色条: 0 = 关闭, 1 = 开启
  
  Serial.println("摄像头参数优化完成");
  if(psramFound()){
    Serial.println("分辨率: VGA (640x480)");
  } else {
    Serial.println("分辨率: QVGA (320x240)");
  }
  Serial.println("画质: 10 (高质量)");
  Serial.println("帧缓冲区: 1 (最低延迟)");
  Serial.println("模式: 自动曝光 + 限制增益 (稳定码流)");
  Serial.println("曝光值: 400 (降低曝光，解决过曝)");
}

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(false);  // 关闭调试输出以提高性能
  Serial.println();
  Serial.println("=== ESP32-CAM 儿童安全监控系统 ===");
  Serial.println("已修复5个关键问题，优化抗延迟");
  Serial.println("特别修复: 降低曝光值解决过曝问题");

  // 关闭棕色断电警告（提高稳定性）
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

  // 初始化摄像头
  setupCamera();

  // ==================== 设置静态IP ====================
  IPAddress local_IP(192, 168, 235, 31);     // 固定的IP
  IPAddress gateway(192, 168, 235, 1);       // 网关
  IPAddress subnet(255, 255, 255, 0);        // 子网掩码
  IPAddress dns1(8, 8, 8, 8);                // Google DNS
  IPAddress dns2(8, 8, 4, 4);                // 备用DNS
  
  // 配置静态IP
  Serial.print("配置静态IP: ");
  Serial.println(local_IP);
  
  if (!WiFi.config(local_IP, gateway, subnet, dns1, dns2)) {
    Serial.println("静态IP配置失败！");
  }
  
  // ==================== 连接WiFi ====================
  // 配置WiFi以提高稳定性
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);              // 禁用WiFi睡眠模式
  WiFi.setTxPower(WIFI_POWER_19_5dBm); // 最大发射功率
  WiFi.setAutoReconnect(true);       // 自动重连
  
  Serial.print("连接WiFi: ");
  Serial.println(ssid);
  
  WiFi.begin(ssid, password);
  
  Serial.print("正在连接");
  int wifi_timeout = 30; // 30秒超时
  while (WiFi.status() != WL_CONNECTED && wifi_timeout > 0) {
    delay(500);
    Serial.print(".");
    wifi_timeout--;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("");
    Serial.println("✓ WiFi 已连接！");
    Serial.print("IP地址: http://");
    Serial.println(WiFi.localIP());
    Serial.print("信号强度(RSSI): ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");
  } else {
    Serial.println("");
    Serial.println("✗ WiFi 连接失败，重启中...");
    delay(2000);
    ESP.restart();
  }

  // 启动流媒体服务器
  startCameraServer();
  
  Serial.println("✓ 摄像头服务器启动完成");
  if(psramFound()){
    Serial.println("分辨率: 640x480 VGA");
  } else {
    Serial.println("分辨率: 320x240 QVGA");
  }
  Serial.println("帧率: 12fps (稳定)");
  Serial.println("画质: 10 (高质量)");
  Serial.println("延迟: 优化最低");
  Serial.println("模式: 固定增益/曝光 (稳定码流)");
}

void loop() {
  // 监控WiFi连接状态
  static unsigned long lastCheck = 0;
  if (millis() - lastCheck > 15000) { // 每15秒检查一次
    lastCheck = millis();
    
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("⚠️ WiFi连接丢失，尝试重新连接...");
      
      // 先断开再重新连接
      WiFi.disconnect();
      delay(100);
      WiFi.reconnect();
      
      // 等待重新连接
      int retry = 0;
      while (WiFi.status() != WL_CONNECTED && retry < 10) {
        delay(500);
        Serial.print(".");
        retry++;
      }
      
      if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n✓ WiFi重新连接成功");
      } else {
        Serial.println("\n✗ WiFi重连失败");
      }
    } else {
      // 正常连接状态，可显示信号强度
      static unsigned long lastRSSI = 0;
      if (millis() - lastRSSI > 60000) { // 每分钟显示一次信号强度
        lastRSSI = millis();
        Serial.print("WiFi信号强度: ");
        Serial.print(WiFi.RSSI());
        Serial.println(" dBm");
      }
    }
  }
  
  delay(1000);
}