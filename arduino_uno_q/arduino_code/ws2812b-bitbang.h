#ifndef WS2812B_BITBANG_H
#define WS2812B_BITBANG_H

#include <zephyr/kernel.h>
#include <zephyr/irq.h>

#define DATA_GPIO_NODE DT_NODELABEL(gpioa)
#define DATA_GPIO_PIN 11  //DI5
#define GPIO_BASE DT_REG_ADDR(DATA_GPIO_NODE)
#define GPIO_BSRR (*(volatile uint32_t *)(GPIO_BASE + 0x18))

#define PIN_HIGH() GPIO_BSRR = (1 << DATA_GPIO_PIN)
#define PIN_LOW() GPIO_BSRR = (1 << (DATA_GPIO_PIN + 16))

const struct device *gpio_dev = DEVICE_DT_GET(DATA_GPIO_NODE);

inline void delay_cycles(uint32_t cycles) __attribute__((always_inline));
inline void delay_cycles(uint32_t cycles) {
  while (cycles--) { __asm__ __volatile__("nop"); }
}

void ws2812b_init() {
  gpio_pin_configure(gpio_dev, DATA_GPIO_PIN, GPIO_OUTPUT_INACTIVE);
  gpio_pin_set_raw(gpio_dev, DATA_GPIO_PIN, 0);
}

void ws2812b_show(uint8_t (*buf)[4], int num_leds) {
  unsigned int key = irq_lock();

  for (int led = 0; led < num_leds; led++) {
    for (int channel = 0; channel < 3; channel++) {  // R, G, B
      uint8_t data = buf[led][channel];
      for (int i = 7; i >= 0; i--) {
        if (data & (1 << i)) {
          PIN_HIGH();
          delay_cycles(35);  // T1H
          PIN_LOW();
          delay_cycles(15);  // T1L
        } else {
          PIN_HIGH();
          delay_cycles(12);  // T0H
          PIN_LOW();
          delay_cycles(38);  // T0L
        }
      }
    }
  }

  irq_unlock(key);
  PIN_LOW();
  delayMicroseconds(60);  // Reset signal
}
#endif
