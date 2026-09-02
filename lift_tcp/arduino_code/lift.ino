#include <Arduino.h>
#include <zephyr/kernel.h>
#include "Arduino_RouterBridge.h"
#include "ws2812b-bitbang.h"

/* --- New Pin Configuration (Low Active) --- */
#define BUTTON_UP A2
#define BUTTON_DOWN A3
#define DOOR_SENSOR 3    //A1
#define SOLENOID_UP 4    // Relay 1 11
#define SOLENOID_DOWN 7  // Relay 2
#define LED_UP 8         // Relay 3
#define LED_DOWN 12      // Relay 4
// Relay Logic
#define RELAY_ON HIGH
#define RELAY_OFF LOW
// door logic
#define DOOR_OPEN HIGH
#define DOOR_CLOSE LOW
// WS2812B Config
#define NUM_LEDS 16
uint8_t framebuffer[NUM_LEDS][4];

/* --- Shared State --- */
volatile int up_sol_state = 0, down_sol_state = 0;
volatile int up_led_latch = 0, down_led_latch = 0;
volatile unsigned long timer_up = 0, timer_down = 0;
volatile unsigned long last_up_press = 0, last_down_press = 0;
const unsigned long PRESS_LOCK_TIME = 5000;
static bool prev_up = HIGH, prev_down = HIGH;
const int AUTO_DURATION = 800;

// LED WS2812B State
volatile float global_brightness = 0.1;
volatile uint8_t target_r = 0, target_g = 0, target_b = 255;
volatile bool led_update_req = true;

/* --- RTOS Stacks --- */
#define STACK_SIZE 2048
K_THREAD_STACK_DEFINE(safety_stack, STACK_SIZE);
K_THREAD_STACK_DEFINE(motor_stack, STACK_SIZE);
K_THREAD_STACK_DEFINE(bridge_stack, 4096);
K_THREAD_STACK_DEFINE(led_stack, STACK_SIZE);

struct k_thread safety_thread_data, motor_thread_data, bridge_thread_data, led_thread_data;

/* --- LED Task & Functions --- */
void update_ws_hardware(uint8_t g, uint8_t r, uint8_t b) {
  for (int i = 0; i < NUM_LEDS; i++) {
    framebuffer[i][0] = (uint8_t)(g * global_brightness);
    framebuffer[i][1] = (uint8_t)(r * global_brightness);
    framebuffer[i][2] = (uint8_t)(b * global_brightness);
    framebuffer[i][3] = 0;
  }
  ws2812b_show(framebuffer, NUM_LEDS);
}

void led_task(void *p1, void *p2, void *p3) {
  ws2812b_init();
  update_ws_hardware(target_g, target_r, target_b);
  for (;;) {
    if (led_update_req) {
      update_ws_hardware(target_g, target_r, target_b);
      led_update_req = false;
    }
    k_msleep(50);
  }
}

/* --- Logic & Safety Task --- */
void safety_task(void *p1, void *p2, void *p3) {
  static bool prevDoorOpen = false;
  for (;;) {
    bool isDoorOpen = (digitalRead(DOOR_SENSOR) == DOOR_OPEN);
    bool current_up = digitalRead(BUTTON_UP);
    bool current_down = digitalRead(BUTTON_DOWN);
    // detect door state change
    if (isDoorOpen != prevDoorOpen) {
      // reset button edge detection
      prev_up = current_up;
      prev_down = current_down;
      // clear outputs when door opens
      if (isDoorOpen) {
        up_led_latch = 0;
        down_led_latch = 0;
      }
      prevDoorOpen = isDoorOpen;
      k_msleep(100);  // anti bounce door sensor
      continue;
    }
    // DOOR OPEN
    if (isDoorOpen) {
      if (current_up == LOW && prev_up == HIGH && millis() - last_up_press > PRESS_LOCK_TIME) {
        last_up_press = millis();
        up_sol_state = 1;
        timer_up = millis();
      }
      if (current_down == LOW && prev_down == HIGH && millis() - last_down_press > PRESS_LOCK_TIME) {
        last_down_press = millis();
        down_sol_state = 1;
        timer_down = millis();
      }
    }
    // DOOR CLOSED
    else {
      if (current_up == LOW && prev_up == HIGH && millis() - last_up_press > PRESS_LOCK_TIME) {
        last_up_press = millis();
        up_sol_state = 1;
        up_led_latch = 1;
        timer_up = millis();
      }
      if (current_down == LOW && prev_down == HIGH && millis() - last_down_press > PRESS_LOCK_TIME) {
        last_down_press = millis();
        down_sol_state = 1;
        down_led_latch = 1;
        timer_down = millis();
      }
    }
    prev_up = current_up;
    prev_down = current_down;
    k_msleep(40);
  }
}

/* --- Output Control Task (high Active Logic) --- */
void motor_task(void *p1, void *p2, void *p3) {
  for (;;) {
    unsigned long now = millis();
    if (up_sol_state && (now - timer_up > AUTO_DURATION)) up_sol_state = 0;
    if (down_sol_state && (now - timer_down > AUTO_DURATION)) down_sol_state = 0;
    // ส่งสัญญาณไปที่ Relay
    digitalWrite(SOLENOID_UP, up_sol_state ? RELAY_ON : RELAY_OFF);
    digitalWrite(SOLENOID_DOWN, down_sol_state ? RELAY_ON : RELAY_OFF);
    // ไฟปุ่มกดจะติดค้างตามค่า Latch (จะดับเมื่อประตูเปิดตาม Logic ใน safety_task)
    digitalWrite(LED_UP, up_led_latch ? RELAY_ON : RELAY_OFF);
    digitalWrite(LED_DOWN, down_led_latch ? RELAY_ON : RELAY_OFF);
    k_msleep(20);
  }
}
/* --- Communication RPC Functions --- */
String set_led_rpc(String payload) {
  int commaIndex = payload.indexOf(',');
  if (commaIndex == -1) return "ERR_FORMAT";
  String color_code = payload.substring(0, commaIndex);
  int bright_val = payload.substring(commaIndex + 1).toInt();
  global_brightness = constrain(bright_val, 0, 100) / 100.0;

  if (color_code == "RED") {
    target_g = 0;
    target_r = 255;
    target_b = 0;
  } else if (color_code == "GREEN") {
    target_g = 255;
    target_r = 0;
    target_b = 0;
  } else if (color_code == "BLUE") {
    target_g = 0;
    target_r = 0;
    target_b = 255;
  } else if (color_code == "YELLOW") {
    target_g = 255;
    target_r = 255;
    target_b = 0;
  } else if (color_code == "ORANGE") {
    target_g = 100;
    target_r = 255;
    target_b = 0;
  } else if (color_code == "PURPLE") {
    target_g = 0;
    target_r = 128;
    target_b = 128;
  } else if (color_code == "PINK") {
    target_g = 50;
    target_r = 255;
    target_b = 100;
  } else if (color_code == "WHITE") {
    target_g = 255;
    target_r = 255;
    target_b = 255;
  } else if (color_code == "OFF") {
    target_g = 0;
    target_r = 0;
    target_b = 0;
  } else {
    return "UNKNOWN_COLOR";
  }

  led_update_req = true;
  return "OK";
}

String move_rpc(String action) {
  if (action == "1") {
    up_sol_state = 1;
    timer_up = millis();
    return "SOL_UP";
  } else if (action == "2") {
    down_sol_state = 1;
    timer_down = millis();
    return "SOL_DOWN";
  } else if (action == "3") {  // Command 3: UP Solenoid + UP Indicator LED
    up_sol_state = 1;
    up_led_latch = 1;
    timer_up = millis();
    return "SOL_AND_LED_UP";
  } else if (action == "4") {  // Command 4: DOWN Solenoid + DOWN Indicator LED
    down_sol_state = 1;
    down_led_latch = 1;
    timer_down = millis();
    return "SOL_AND_LED_DOWN";
  } else if (action == "5") {  // หยุดเฉพาะ Solenoid
    up_sol_state = 0;
    down_sol_state = 0;
    return "STOP_SOL";
  } else if (action == "6") {  // ดับเฉพาะไฟ LED
    up_led_latch = 0;
    down_led_latch = 0;
    return "STOP_LED";
  } else if (action == "0") {  // หยุดทุกอย่าง
    up_sol_state = 0;
    down_sol_state = 0;
    up_led_latch = 0;
    down_led_latch = 0;
    return "STOPPED";
  }
  return "UNKNOWN_COMMAND";
}

String status_rpc(String dummy) {
  String s = "DOOR:" + String(digitalRead(DOOR_SENSOR) == DOOR_CLOSE ? "CLOSED" : "OPEN") + "|";
  s += "FLOOR:2";
  return s;
}

void bridge_task(void *p1, void *p2, void *p3) {
  Bridge.begin();
  Bridge.provide("move", move_rpc);
  Bridge.provide("status", status_rpc);
  Bridge.provide("set_led", set_led_rpc);
  for (;;) {
    Bridge.update();
    k_msleep(10);
  }
}

/* --- Setup & Loop --- */
void setup() {

  pinMode(SOLENOID_UP, OUTPUT);
  pinMode(SOLENOID_DOWN, OUTPUT);
  pinMode(LED_UP, OUTPUT);
  pinMode(LED_DOWN, OUTPUT);

  pinMode(BUTTON_UP, INPUT_PULLUP);
  pinMode(BUTTON_DOWN, INPUT_PULLUP);
  pinMode(DOOR_SENSOR, INPUT_PULLUP);

  digitalWrite(SOLENOID_UP, RELAY_OFF);
  digitalWrite(SOLENOID_DOWN, RELAY_OFF);
  digitalWrite(LED_UP, RELAY_OFF);
  digitalWrite(LED_DOWN, RELAY_OFF);

  k_thread_create(&safety_thread_data, safety_stack, STACK_SIZE, safety_task, NULL, NULL, NULL, 7, 0, K_NO_WAIT);
  k_thread_create(&motor_thread_data, motor_stack, STACK_SIZE, motor_task, NULL, NULL, NULL, 7, 0, K_NO_WAIT);
  k_thread_create(&bridge_thread_data, bridge_stack, 4096, bridge_task, NULL, NULL, NULL, 7, 0, K_NO_WAIT);
  k_thread_create(&led_thread_data, led_stack, STACK_SIZE, led_task, NULL, NULL, NULL, 7, 0, K_NO_WAIT);
}

void loop() {
  k_msleep(1000);
}