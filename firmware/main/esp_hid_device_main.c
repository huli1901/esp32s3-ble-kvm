/*
 * SPDX-FileCopyrightText: 2021-2024 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Unlicense OR CC0-1.0
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "driver/usb_serial_jtag.h"
#include "driver/rmt_tx.h"
#include "driver/gpio.h"
#include "nvs_flash.h"

/* 多连接支持（实现在 components/esp_hid/src/nimble_hidd.c） */
extern uint8_t nimble_hidd_conn_count(void);
extern uint16_t nimble_hidd_conn_at(uint8_t slot);
extern uint16_t nimble_hidd_target(void);
extern bool nimble_hidd_set_target_slot(uint8_t slot);
#include "esp_bt.h"

#if CONFIG_BT_NIMBLE_ENABLED
#include "host/ble_hs.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
/* 命令下发笔记本（GATT 0xFF02 notify），定义在 esp_hid_gap.c */
void kvm_gatt_send_cmd(uint8_t cmd);
#else
#include "esp_bt_defs.h"
#if CONFIG_BT_BLE_ENABLED
#include "esp_gap_ble_api.h"
#include "esp_gatts_api.h"
#include "esp_gatt_defs.h"
#endif
#include "esp_bt_main.h"
#include "esp_bt_device.h"
#if CONFIG_BT_SDP_COMMON_ENABLED
#include "esp_sdp_api.h"
#endif /* CONFIG_BT_SDP_COMMON_ENABLED */
#endif

#include "esp_hidd.h"
#include "esp_hid_gap.h"

static const char *TAG = "HID_DEV_DEMO";

typedef struct
{
    TaskHandle_t task_hdl;
    esp_hidd_dev_t *hid_dev;
    uint8_t protocol_mode;
    uint8_t *buffer;
} local_param_t;

#if CONFIG_BT_BLE_ENABLED || CONFIG_BT_NIMBLE_ENABLED
static local_param_t s_ble_hid_param = {0};

const unsigned char mediaReportMap[] = {
    0x05, 0x0C,        // Usage Page (Consumer)
    0x09, 0x01,        // Usage (Consumer Control)
    0xA1, 0x01,        // Collection (Application)
    0x85, 0x03,        //   Report ID (3)
    0x09, 0x02,        //   Usage (Numeric Key Pad)
    0xA1, 0x02,        //   Collection (Logical)
    0x05, 0x09,        //     Usage Page (Button)
    0x19, 0x01,        //     Usage Minimum (0x01)
    0x29, 0x0A,        //     Usage Maximum (0x0A)
    0x15, 0x01,        //     Logical Minimum (1)
    0x25, 0x0A,        //     Logical Maximum (10)
    0x75, 0x04,        //     Report Size (4)
    0x95, 0x01,        //     Report Count (1)
    0x81, 0x00,        //     Input (Data,Array,Abs,No Wrap,Linear,Preferred State,No Null Position)
    0xC0,              //   End Collection
    0x05, 0x0C,        //   Usage Page (Consumer)
    0x09, 0x86,        //   Usage (Channel)
    0x15, 0xFF,        //   Logical Minimum (-1)
    0x25, 0x01,        //   Logical Maximum (1)
    0x75, 0x02,        //   Report Size (2)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x46,        //   Input (Data,Var,Rel,No Wrap,Linear,Preferred State,Null State)
    0x09, 0xE9,        //   Usage (Volume Increment)
    0x09, 0xEA,        //   Usage (Volume Decrement)
    0x15, 0x00,        //   Logical Minimum (0)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x02,        //   Report Count (2)
    0x81, 0x02,        //   Input (Data,Var,Abs,No Wrap,Linear,Preferred State,No Null Position)
    0x09, 0xE2,        //   Usage (Mute)
    0x09, 0x30,        //   Usage (Power)
    0x09, 0x83,        //   Usage (Recall Last)
    0x09, 0x81,        //   Usage (Assign Selection)
    0x09, 0xB0,        //   Usage (Play)
    0x09, 0xB1,        //   Usage (Pause)
    0x09, 0xB2,        //   Usage (Record)
    0x09, 0xB3,        //   Usage (Fast Forward)
    0x09, 0xB4,        //   Usage (Rewind)
    0x09, 0xB5,        //   Usage (Scan Next Track)
    0x09, 0xB6,        //   Usage (Scan Previous Track)
    0x09, 0xB7,        //   Usage (Stop)
    0x15, 0x01,        //   Logical Minimum (1)
    0x25, 0x0C,        //   Logical Maximum (12)
    0x75, 0x04,        //   Report Size (4)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x00,        //   Input (Data,Array,Abs,No Wrap,Linear,Preferred State,No Null Position)
    0x09, 0x80,        //   Usage (Selection)
    0xA1, 0x02,        //   Collection (Logical)
    0x05, 0x09,        //     Usage Page (Button)
    0x19, 0x01,        //     Usage Minimum (0x01)
    0x29, 0x03,        //     Usage Maximum (0x03)
    0x15, 0x01,        //     Logical Minimum (1)
    0x25, 0x03,        //     Logical Maximum (3)
    0x75, 0x02,        //     Report Size (2)
    0x81, 0x00,        //     Input (Data,Array,Abs,No Wrap,Linear,Preferred State,No Null Position)
    0xC0,              //   End Collection
    0x81, 0x03,        //   Input (Const,Var,Abs,No Wrap,Linear,Preferred State,No Null Position)
    0xC0,              // End Collection
};
#if CONFIG_EXAMPLE_HID_DEVICE_ROLE && CONFIG_EXAMPLE_HID_DEVICE_ROLE == 3
const unsigned char mouseReportMap[] = {
    0x05, 0x01,                    // USAGE_PAGE (Generic Desktop)
    0x09, 0x02,                    // USAGE (Mouse)
    0xa1, 0x01,                    // COLLECTION (Application)

    0x09, 0x01,                    //   USAGE (Pointer)
    0xa1, 0x00,                    //   COLLECTION (Physical)

    0x05, 0x09,                    //     USAGE_PAGE (Button)
    0x19, 0x01,                    //     USAGE_MINIMUM (Button 1)
    0x29, 0x03,                    //     USAGE_MAXIMUM (Button 3)
    0x15, 0x00,                    //     LOGICAL_MINIMUM (0)
    0x25, 0x01,                    //     LOGICAL_MAXIMUM (1)
    0x95, 0x03,                    //     REPORT_COUNT (3)
    0x75, 0x01,                    //     REPORT_SIZE (1)
    0x81, 0x02,                    //     INPUT (Data,Var,Abs)
    0x95, 0x01,                    //     REPORT_COUNT (1)
    0x75, 0x05,                    //     REPORT_SIZE (5)
    0x81, 0x03,                    //     INPUT (Cnst,Var,Abs)

    0x05, 0x01,                    //     USAGE_PAGE (Generic Desktop)
    0x09, 0x30,                    //     USAGE (X)
    0x09, 0x31,                    //     USAGE (Y)
    0x09, 0x38,                    //     USAGE (Wheel)
    0x15, 0x81,                    //     LOGICAL_MINIMUM (-127)
    0x25, 0x7f,                    //     LOGICAL_MAXIMUM (127)
    0x75, 0x08,                    //     REPORT_SIZE (8)
    0x95, 0x03,                    //     REPORT_COUNT (3)
    0x81, 0x06,                    //     INPUT (Data,Var,Rel)

    0xc0,                          //   END_COLLECTION
    0xc0                           // END_COLLECTION
};
// send the buttons, change in x, and change in y
void send_mouse(uint8_t buttons, char dx, char dy, char wheel)
{
    static uint8_t buffer[4] = {0};
    buffer[0] = buttons;
    buffer[1] = dx;
    buffer[2] = dy;
    buffer[3] = wheel;
    esp_hidd_dev_input_set(s_ble_hid_param.hid_dev, 0, 0, buffer, 4);
}

void ble_hid_demo_task_mouse(void *pvParameters)
{
    static const char* help_string = "########################################################################\n"\
    "BT hid mouse demo usage:\n"\
    "You can input these value to simulate mouse: 'q', 'w', 'e', 'a', 's', 'd', 'h'\n"\
    "q -- click the left key\n"\
    "w -- move up\n"\
    "e -- click the right key\n"\
    "a -- move left\n"\
    "s -- move down\n"\
    "d -- move right\n"\
    "h -- show the help\n"\
    "########################################################################\n";
    printf("%s\n", help_string);
    char c;
    while (1) {
        c = fgetc(stdin);
        switch (c) {
        case 'q':
            send_mouse(1, 0, 0, 0);
            break;
        case 'w':
            send_mouse(0, 0, -10, 0);
            break;
        case 'e':
            send_mouse(2, 0, 0, 0);
            break;
        case 'a':
            send_mouse(0, -10, 0, 0);
            break;
        case 's':
            send_mouse(0, 0, 10, 0);
            break;
        case 'd':
            send_mouse(0, 10, 0, 0);
            break;
        case 'h':
            printf("%s\n", help_string);
            break;
        default:
            break;
        }
        vTaskDelay(10 / portTICK_PERIOD_MS);
    }
}
#endif

#if CONFIG_EXAMPLE_HID_DEVICE_ROLE && CONFIG_EXAMPLE_HID_DEVICE_ROLE == 2
#define CASE(a, b, c)  \
                case a: \
				buffer[0] = b;  \
				buffer[2] = c; \
                break;\

// USB keyboard codes
#define USB_HID_MODIFIER_LEFT_CTRL      0x01
#define USB_HID_MODIFIER_LEFT_SHIFT     0x02
#define USB_HID_MODIFIER_LEFT_ALT       0x04
#define USB_HID_MODIFIER_RIGHT_CTRL     0x10
#define USB_HID_MODIFIER_RIGHT_SHIFT    0x20
#define USB_HID_MODIFIER_RIGHT_ALT      0x40

#define USB_HID_SPACE                   0x2C
#define USB_HID_DOT                     0x37
#define USB_HID_NEWLINE                 0x28
#define USB_HID_FSLASH                  0x38
#define USB_HID_BSLASH                  0x31
#define USB_HID_COMMA                   0x36
#define USB_HID_DOT                     0x37

const unsigned char keyboardReportMap[] = { //7 bytes input (modifiers, resrvd, keys*5), 1 byte output
    0x05, 0x01,        // Usage Page (Generic Desktop Ctrls)
    0x09, 0x06,        // Usage (Keyboard)
    0xA1, 0x01,        // Collection (Application)
    0x85, 0x01,        //   Report ID (1)
    0x05, 0x07,        //   Usage Page (Kbrd/Keypad)
    0x19, 0xE0,        //   Usage Minimum (0xE0)
    0x29, 0xE7,        //   Usage Maximum (0xE7)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x08,        //   Report Count (8)
    0x81, 0x02,        //   Input (Data,Var,Abs,No Wrap,Linear,Preferred State,No Null Position)
    0x95, 0x01,        //   Report Count (1)
    0x75, 0x08,        //   Report Size (8)
    0x81, 0x03,        //   Input (Const,Var,Abs,No Wrap,Linear,Preferred State,No Null Position)
    0x95, 0x05,        //   Report Count (5)
    0x75, 0x01,        //   Report Size (1)
    0x05, 0x08,        //   Usage Page (LEDs)
    0x19, 0x01,        //   Usage Minimum (Num Lock)
    0x29, 0x05,        //   Usage Maximum (Kana)
    0x91, 0x02,        //   Output (Data,Var,Abs,No Wrap,Linear,Preferred State,No Null Position,Non-volatile)
    0x95, 0x01,        //   Report Count (1)
    0x75, 0x03,        //   Report Size (3)
    0x91, 0x03,        //   Output (Const,Var,Abs,No Wrap,Linear,Preferred State,No Null Position,Non-volatile)
    0x95, 0x05,        //   Report Count (5)
    0x75, 0x08,        //   Report Size (8)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x65,        //   Logical Maximum (101)
    0x05, 0x07,        //   Usage Page (Kbrd/Keypad)
    0x19, 0x00,        //   Usage Minimum (0x00)
    0x29, 0x65,        //   Usage Maximum (0x65)
    0x81, 0x00,        //   Input (Data,Array,Abs,No Wrap,Linear,Preferred State,No Null Position)
    0xC0,              // End Collection

    // 65 bytes
};

#if CONFIG_EXAMPLE_HID_DEVICE_ROLE == 2
/* KVM 桥用复合描述符：键盘 Report ID 1（7字节） + 鼠标 Report ID 2（4字节） */
static const unsigned char comboReportMap[] = {
    /* ---- Keyboard, Report ID 1: mod(8b) + reserved(8b) + 5 key slots ---- */
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x06,        // Usage (Keyboard)
    0xA1, 0x01,        // Collection (Application)
    0x85, 0x01,        //   Report ID (1)
    0x05, 0x07,        //   Usage Page (Keyboard)
    0x19, 0xE0,        //   Usage Minimum (0xE0)
    0x29, 0xE7,        //   Usage Maximum (0xE7)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x08,        //   Report Count (8)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x95, 0x01,        //   Report Count (1)
    0x75, 0x08,        //   Report Size (8)
    0x81, 0x03,        //   Input (Const)            reserved byte
    0x95, 0x05,        //   Report Count (5)
    0x75, 0x08,        //   Report Size (8)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x65,        //   Logical Maximum (101)
    0x05, 0x07,        //   Usage Page (Keyboard)
    0x19, 0x00,        //   Usage Minimum (0)
    0x29, 0x65,        //   Usage Maximum (101)
    0x81, 0x00,        //   Input (Data,Array,Abs)   5 key slots
    /* LED Output Report(Report ID 1)：标准 HID 通道，兼作反向信号。
       bit3(Kana) 由笔记本端置位表示"切回台式机"，Windows 不会主动发这一位 */
    0x95, 0x05,        //   Report Count (5)
    0x75, 0x01,        //   Report Size (1)
    0x05, 0x08,        //   Usage Page (LEDs)
    0x19, 0x01,        //   Usage Minimum (NumLock)
    0x29, 0x05,        //   Usage Maximum (Kana)
    0x91, 0x02,        //   Output (Data,Var,Abs)
    0x95, 0x01,        //   Report Count (1)
    0x75, 0x03,        //   Report Size (3)
    0x91, 0x03,        //   Output (Const) padding
    0xC0,              // End Collection

    /* ---- Mouse, Report ID 2: buttons(3b)+pad(5b) + dx + dy + wheel ---- */
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x02,        // Usage (Mouse)
    0xA1, 0x01,        // Collection (Application)
    0x85, 0x02,        //   Report ID (2)
    0x09, 0x01,        //   Usage (Pointer)
    0xA1, 0x00,        //   Collection (Physical)
    0x05, 0x09,        //     Usage Page (Button)
    0x19, 0x01,        //     Usage Minimum (1)
    0x29, 0x03,        //     Usage Maximum (3)      左/右/中三键
    0x15, 0x00,        //     Logical Minimum (0)
    0x25, 0x01,        //     Logical Maximum (1)
    0x95, 0x03,        //     Report Count (3)
    0x75, 0x01,        //     Report Size (1)
    0x81, 0x02,        //     Input (Data,Var,Abs)
    0x95, 0x01,        //     Report Count (1)
    0x75, 0x05,        //     Report Size (5)
    0x81, 0x03,        //     Input (Const)           padding
    0x05, 0x01,        //     Usage Page (Generic Desktop)
    0x09, 0x30,        //     Usage (X)
    0x09, 0x31,        //     Usage (Y)
    0x09, 0x38,        //     Usage (Wheel)
    0x15, 0x81,        //     Logical Minimum (-127)
    0x25, 0x7F,        //     Logical Maximum (127)
    0x75, 0x08,        //     Report Size (8)
    0x95, 0x03,        //     Report Count (3)
    0x81, 0x06,        //     Input (Data,Var,Rel)
    0xC0,              //   End Collection
    0xC0,              // End Collection
};
#endif

/* ---------- 板载 WS2812 状态灯（官方 DevKitC-1 为 GPIO48） ---------- */
#define KVM_LED_GPIO          48
#define KVM_LED_COLOR_LOCAL   0x200000   /* 绿：控台式机 */
#define KVM_LED_COLOR_SLOT0   0x000020   /* 蓝：控第 1 台外接设备 */
#define KVM_LED_COLOR_SLOT1   0x200020   /* 紫：控第 2 台外接设备 */
#define KVM_LED_COLOR_OFFLINE 0x002000   /* 红：BLE 未连接 */

/* 0=本地(台式机)  1=slot0  2=slot1 */
static uint8_t s_kvm_remote = 0;
/* BLE 是否已断开：断开时红灯常亮，不参与呼吸。
   上电默认 1（无连接），收到 CONNECT 才清零 */
static volatile uint8_t s_kvm_offline = 1;

static rmt_channel_handle_t s_led_chan = NULL;
static rmt_encoder_handle_t s_led_enc = NULL;

static void kvm_led_init(void)
{
    rmt_tx_channel_config_t tx_cfg = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .gpio_num = KVM_LED_GPIO,
        .mem_block_symbols = 64,
        .resolution_hz = 10 * 1000 * 1000,   /* 0.1us/tick */
        .trans_queue_depth = 4,
    };
    if (rmt_new_tx_channel(&tx_cfg, &s_led_chan) != ESP_OK) {
        ESP_LOGE("KVM_LED", "rmt_new_tx_channel failed");
        return;
    }
    /* WS2812 时序：T0H=0.4us T0L=0.9us / T1H=0.9us T1L=0.4us */
    rmt_bytes_encoder_config_t enc_cfg = {
        .bit0 = { .level0 = 1, .duration0 = 3, .level1 = 0, .duration1 = 9 },
        .bit1 = { .level0 = 1, .duration0 = 9, .level1 = 0, .duration1 = 3 },
        .flags.msb_first = 1,
    };
    if (rmt_new_bytes_encoder(&enc_cfg, &s_led_enc) != ESP_OK) {
        ESP_LOGE("KVM_LED", "rmt_new_bytes_encoder failed");
        return;
    }
    rmt_enable(s_led_chan);
    ESP_LOGI("KVM_LED", "WS2812 ready on GPIO%d", KVM_LED_GPIO);
}

static void kvm_led_set(uint32_t grb)
{
    if (!s_led_chan || !s_led_enc) {
        return;
    }
    uint8_t data[3] = { (grb >> 16) & 0xFF, (grb >> 8) & 0xFF, grb & 0xFF };
    rmt_transmit_config_t tx_cfg = { .loop_count = 0 };
    rmt_transmit(s_led_chan, s_led_enc, data, sizeof(data), &tx_cfg);
    rmt_tx_wait_all_done(s_led_chan, 100);
}

static uint32_t kvm_color_for(uint8_t target)
{
    if (target == 0) {
        return KVM_LED_COLOR_LOCAL;
    }
    return (target == 1) ? KVM_LED_COLOR_SLOT0 : KVM_LED_COLOR_SLOT1;
}

static void kvm_led_state(uint8_t target)
{
    kvm_led_set(kvm_color_for(target));
}

/* 空闲呼吸：超过 60 秒没有任何输入/切换，LED 缓慢渐变提示待机 */
static volatile uint32_t s_last_activity = 0;

static uint32_t kvm_color_scale(uint32_t grb, uint8_t lvl, uint8_t base_max)
{
    uint32_t g = ((grb >> 16) & 0xFF) * lvl / base_max;
    uint32_t r = ((grb >> 8) & 0xFF) * lvl / base_max;
    uint32_t b = (grb & 0xFF) * lvl / base_max;
    return (g << 16) | (r << 8) | b;
}

static void kvm_led_task(void *arg)
{
    const uint32_t IDLE_MS = 60000;
    uint32_t phase = 0;
    uint8_t breathing = 0;

    while (1) {
        if (s_kvm_offline) {
            kvm_led_set(KVM_LED_COLOR_OFFLINE);   /* 断开：红灯常亮 */
            breathing = 0;
            vTaskDelay(pdMS_TO_TICKS(200));       /* 低频轮询，省 RMT 调用 */
            continue;
        }
        uint32_t now_ms = xTaskGetTickCount() * portTICK_PERIOD_MS;
        if (now_ms - s_last_activity > IDLE_MS) {
            /* 三角波 3 秒一个周期，亮度 0 ~ 28 */
            phase = (phase + 40) % 3000;
            uint32_t tri = phase < 1500 ? phase : (3000 - phase);
            uint8_t lvl = (uint8_t)(tri * 28 / 1500);
            uint32_t base = kvm_color_for(s_kvm_remote);
            kvm_led_set(kvm_color_scale(base, lvl, 0x20));
            breathing = 1;
        } else if (breathing) {
            kvm_led_state(s_kvm_remote);   /* 有活动了，恢复常亮 */
            breathing = 0;
        }
        vTaskDelay(pdMS_TO_TICKS(40));
    }
}

/* ---------- BOOT 键（GPIO0）作为物理切换键 ----------
   不经过 Windows 键盘栈，键盘钩子失效时依然可用。 */
#define KVM_BOOT_GPIO   0

static void kvm_boot_btn_task(void *arg)
{
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << KVM_BOOT_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);

    int last = 1;
    while (1) {
        int now = gpio_get_level(KVM_BOOT_GPIO);
        if (last == 1 && now == 0) {
            vTaskDelay(pdMS_TO_TICKS(30));          /* 消抖 */
            if (gpio_get_level(KVM_BOOT_GPIO) == 0) {
                uint8_t rev[4] = { 0xAA, 0x03, 0x01, 0x01 };
                usb_serial_jtag_write_bytes(rev, sizeof(rev), pdMS_TO_TICKS(5));
                ESP_LOGI("KVM_BTN", "BOOT pressed -> request toggle");
            }
        }
        last = now;
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

static void char_to_code(uint8_t *buffer, char ch)
{
	// Check if lower or upper case
	if(ch >= 'a' && ch <= 'z')
	{
		buffer[0] = 0;
		// convert ch to HID letter, starting at a = 4
		buffer[2] = (uint8_t)(4 + (ch - 'a'));
	}
	else if(ch >= 'A' && ch <= 'Z')
	{
		// Add left shift
		buffer[0] = USB_HID_MODIFIER_LEFT_SHIFT;
		// convert ch to lower case
		ch = ch - ('A'-'a');
		// convert ch to HID letter, starting at a = 4
		buffer[2] = (uint8_t)(4 + (ch - 'a'));
	}
	else if(ch >= '0' && ch <= '9') // Check if number
	{
		buffer[0] = 0;
		// convert ch to HID number, starting at 1 = 30, 0 = 39
		if(ch == '0')
		{
			buffer[2] = 39;
		}
		else
		{
			buffer[2] = (uint8_t)(30 + (ch - '1'));
		}
	}
	else // not a letter nor a number
	{
		switch(ch)
		{
            CASE(' ', 0, USB_HID_SPACE);
			CASE('.', 0,USB_HID_DOT);
            CASE('\n', 0, USB_HID_NEWLINE);
			CASE('?', USB_HID_MODIFIER_LEFT_SHIFT, USB_HID_FSLASH);
			CASE('/', 0 ,USB_HID_FSLASH);
			CASE('\\', 0, USB_HID_BSLASH);
			CASE('|', USB_HID_MODIFIER_LEFT_SHIFT, USB_HID_BSLASH);
			CASE(',', 0, USB_HID_COMMA);
			CASE('<', USB_HID_MODIFIER_LEFT_SHIFT, USB_HID_COMMA);
			CASE('>', USB_HID_MODIFIER_LEFT_SHIFT, USB_HID_COMMA);
			CASE('@', USB_HID_MODIFIER_LEFT_SHIFT, 31);
			CASE('!', USB_HID_MODIFIER_LEFT_SHIFT, 30);
			CASE('#', USB_HID_MODIFIER_LEFT_SHIFT, 32);
			CASE('$', USB_HID_MODIFIER_LEFT_SHIFT, 33);
			CASE('%', USB_HID_MODIFIER_LEFT_SHIFT, 34);
			CASE('^', USB_HID_MODIFIER_LEFT_SHIFT,35);
			CASE('&', USB_HID_MODIFIER_LEFT_SHIFT, 36);
			CASE('*', USB_HID_MODIFIER_LEFT_SHIFT, 37);
			CASE('(', USB_HID_MODIFIER_LEFT_SHIFT, 38);
			CASE(')', USB_HID_MODIFIER_LEFT_SHIFT, 39);
			CASE('-', 0, 0x2D);
			CASE('_', USB_HID_MODIFIER_LEFT_SHIFT, 0x2D);
			CASE('=', 0, 0x2E);
			CASE('+', USB_HID_MODIFIER_LEFT_SHIFT, 39);
			CASE(8, 0, 0x2A); // backspace
			CASE('\t', 0, 0x2B);
			default:
				buffer[0] = 0;
				buffer[2] = 0;
		}
	}
}

void send_keyboard(char c)
{
    static uint8_t buffer[8] = {0};
    char_to_code(buffer, c);
    esp_hidd_dev_input_set(s_ble_hid_param.hid_dev, 0, 1, buffer, 7);
    /* send the keyrelease event with sufficient delay */
    vTaskDelay(50 / portTICK_PERIOD_MS);
    memset(buffer, 0, sizeof(uint8_t) * 8);
    esp_hidd_dev_input_set(s_ble_hid_param.hid_dev, 0, 1, buffer, 7);
}

void ble_hid_demo_task_kbd(void *pvParameters)
{
    static const char* help_string = "########################################################################\n"\
                                      "BT hid keyboard demo usage:\n"\
                                      "########################################################################\n";
                                    /* TODO : Add support for function keys and ctrl, alt, esc, etc. */
    /* 等 GATT 服务发现完成后再补报一次，确保 Battery Level 通知能到达主机 */
    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_hidd_dev_battery_set(s_ble_hid_param.hid_dev, 100);
    printf("%s\n", help_string);
    /* 直接用底层驱动读 USB-Serial/JTAG，绕开 VFS stdin */
    usb_serial_jtag_driver_config_t uj_cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    esp_err_t uj_ret = usb_serial_jtag_driver_install(&uj_cfg);
    ESP_LOGI("KBD_RX", "usb_serial_jtag install: %s", esp_err_to_name(uj_ret));

    uint8_t rx_buf[64];
    uint8_t frame[8];
    int f_state = 0;   /* 0=找帧头 1=等类型 2=收payload */
    uint8_t f_type = 0;
    int f_need = 0;
    int f_pos = 0;
    int err_cnt = 0;

    while (1) {
        int len = usb_serial_jtag_read_bytes(rx_buf, sizeof(rx_buf), pdMS_TO_TICKS(10));
        if (len < 0) {
            if (err_cnt < 5) {
                ESP_LOGW("KVM", "read error: %d", len);
                err_cnt++;
            }
            continue;
        }
        if (len == 0) {
            continue;
        }
        err_cnt = 0;

        for (int i = 0; i < len; i++) {
            uint8_t b = rx_buf[i];

            if (f_state == 0) {
                if (b == 0xAA) {
                    f_state = 1;
                }
                continue;
            }
            if (f_state == 1) {
                f_type = b;
                if (b == 0x01) {
                    f_need = 7;
                    f_pos = 0;
                    f_state = 2;
                } else if (b == 0x02) {
                    f_need = 4;
                    f_pos = 0;
                    f_state = 2;
                } else if (b == 0x04) {
                    f_need = 1;      /* AA 04 <0|1>   LED 状态 */
                    f_pos = 0;
                    f_state = 2;
                } else if (b == 0x05) {
                    f_need = 1;      /* AA 05 <slot>  切换输入目标 */
                    f_pos = 0;
                    f_state = 2;
                } else if (b == 0x07) {
                    f_need = 1;      /* AA 07 <cmd>  命令下发笔记本(GATT 0xFF02 notify) */
                    f_pos = 0;
                    f_state = 2;
                } else {
                    f_state = 0;
                }
                continue;
            }
            frame[f_pos++] = b;
            if (f_pos >= f_need) {
                s_last_activity = xTaskGetTickCount();   /* 有输入就重置空闲计时 */
                if (f_type == 0x01) {
                    /* 键盘：Report ID 1，7 字节整报告透传 */
                    esp_hidd_dev_input_set(s_ble_hid_param.hid_dev, 0, 1, frame, 7);
                } else if (f_type == 0x02) {
                    /* 鼠标：Report ID 2，btn + dx + dy + wheel */
                    esp_hidd_dev_input_set(s_ble_hid_param.hid_dev, 0, 2, frame, 4);
                } else if (f_type == 0x04) {
                    /* LED 状态：0=本地(绿) 1=slot0(蓝) 2=slot1(紫) */
                    s_kvm_remote = frame[0];
                    kvm_led_state(s_kvm_remote);
                } else if (f_type == 0x05) {
                    /* 切换输入目标连接，回 ACK: AA 06 <slot|0xFF> <count> */
                    bool ok = nimble_hidd_set_target_slot(frame[0]);
                    uint8_t ack[4] = { 0xAA, 0x06, ok ? frame[0] : 0xFF,
                                       nimble_hidd_conn_count() };
                    usb_serial_jtag_write_bytes(ack, sizeof(ack), pdMS_TO_TICKS(5));
                    ESP_LOGI("KVM", "target slot %u -> %s (count=%u)", frame[0],
                             ok ? "OK" : "FAIL", nimble_hidd_conn_count());
                } else if (f_type == 0x07) {
                    /* 命令下发笔记本：GATT 0xFF02 notify */
                    kvm_gatt_send_cmd(frame[0]);
                    ESP_LOGI("KVM", "cmd -> laptop: %u", frame[0]);
                }
                f_state = 0;
                f_pos = 0;
            }
        }
    }
}
#endif
static esp_hid_raw_report_map_t ble_report_maps[] = {
#if !CONFIG_BT_NIMBLE_ENABLED || CONFIG_EXAMPLE_HID_DEVICE_ROLE == 1
    /* This block is compiled for bluedroid as well */
    {
        .data = mediaReportMap,
        .len = sizeof(mediaReportMap)
    }
#elif CONFIG_EXAMPLE_HID_DEVICE_ROLE && CONFIG_EXAMPLE_HID_DEVICE_ROLE == 2
    {
        .data = comboReportMap,
        .len = sizeof(comboReportMap)
    },
#elif CONFIG_EXAMPLE_HID_DEVICE_ROLE && CONFIG_EXAMPLE_HID_DEVICE_ROLE == 3
    {
        .data = mouseReportMap,
        .len = sizeof(mouseReportMap)
    },
#endif
};

static esp_hid_device_config_t ble_hid_config = {
    .vendor_id          = 0x16C0,
    .product_id         = 0x05DF,
    .version            = 0x0100,
#if CONFIG_EXAMPLE_HID_DEVICE_ROLE == 2
    .device_name        = "ESP Keyboard",
#elif CONFIG_EXAMPLE_HID_DEVICE_ROLE == 3
    .device_name        = "ESP Mouse",
#else
    .device_name        = "ESP BLE HID2",
#endif
    .manufacturer_name  = "Espressif",
    .serial_number      = "1234567890",
    .report_maps        = ble_report_maps,
    .report_maps_len    = 1
};

#define HID_CC_RPT_MUTE                 1
#define HID_CC_RPT_POWER                2
#define HID_CC_RPT_LAST                 3
#define HID_CC_RPT_ASSIGN_SEL           4
#define HID_CC_RPT_PLAY                 5
#define HID_CC_RPT_PAUSE                6
#define HID_CC_RPT_RECORD               7
#define HID_CC_RPT_FAST_FWD             8
#define HID_CC_RPT_REWIND               9
#define HID_CC_RPT_SCAN_NEXT_TRK        10
#define HID_CC_RPT_SCAN_PREV_TRK        11
#define HID_CC_RPT_STOP                 12

#define HID_CC_RPT_CHANNEL_UP           0x10
#define HID_CC_RPT_CHANNEL_DOWN         0x30
#define HID_CC_RPT_VOLUME_UP            0x40
#define HID_CC_RPT_VOLUME_DOWN          0x80

// HID Consumer Control report bitmasks
#define HID_CC_RPT_NUMERIC_BITS         0xF0
#define HID_CC_RPT_CHANNEL_BITS         0xCF
#define HID_CC_RPT_VOLUME_BITS          0x3F
#define HID_CC_RPT_BUTTON_BITS          0xF0
#define HID_CC_RPT_SELECTION_BITS       0xCF

// Macros for the HID Consumer Control 2-byte report
#define HID_CC_RPT_SET_NUMERIC(s, x)    (s)[0] &= HID_CC_RPT_NUMERIC_BITS;   (s)[0] = (x)
#define HID_CC_RPT_SET_CHANNEL(s, x)    (s)[0] &= HID_CC_RPT_CHANNEL_BITS;   (s)[0] |= ((x) & 0x03) << 4
#define HID_CC_RPT_SET_VOLUME_UP(s)     (s)[0] &= HID_CC_RPT_VOLUME_BITS;    (s)[0] |= 0x40
#define HID_CC_RPT_SET_VOLUME_DOWN(s)   (s)[0] &= HID_CC_RPT_VOLUME_BITS;    (s)[0] |= 0x80
#define HID_CC_RPT_SET_BUTTON(s, x)     (s)[1] &= HID_CC_RPT_BUTTON_BITS;    (s)[1] |= (x)
#define HID_CC_RPT_SET_SELECTION(s, x)  (s)[1] &= HID_CC_RPT_SELECTION_BITS; (s)[1] |= ((x) & 0x03) << 4

// HID Consumer Usage IDs (subset of the codes available in the USB HID Usage Tables spec)
#define HID_CONSUMER_POWER          48  // Power
#define HID_CONSUMER_RESET          49  // Reset
#define HID_CONSUMER_SLEEP          50  // Sleep

#define HID_CONSUMER_MENU           64  // Menu
#define HID_CONSUMER_SELECTION      128 // Selection
#define HID_CONSUMER_ASSIGN_SEL     129 // Assign Selection
#define HID_CONSUMER_MODE_STEP      130 // Mode Step
#define HID_CONSUMER_RECALL_LAST    131 // Recall Last
#define HID_CONSUMER_QUIT           148 // Quit
#define HID_CONSUMER_HELP           149 // Help
#define HID_CONSUMER_CHANNEL_UP     156 // Channel Increment
#define HID_CONSUMER_CHANNEL_DOWN   157 // Channel Decrement

#define HID_CONSUMER_PLAY           176 // Play
#define HID_CONSUMER_PAUSE          177 // Pause
#define HID_CONSUMER_RECORD         178 // Record
#define HID_CONSUMER_FAST_FORWARD   179 // Fast Forward
#define HID_CONSUMER_REWIND         180 // Rewind
#define HID_CONSUMER_SCAN_NEXT_TRK  181 // Scan Next Track
#define HID_CONSUMER_SCAN_PREV_TRK  182 // Scan Previous Track
#define HID_CONSUMER_STOP           183 // Stop
#define HID_CONSUMER_EJECT          184 // Eject
#define HID_CONSUMER_RANDOM_PLAY    185 // Random Play
#define HID_CONSUMER_SELECT_DISC    186 // Select Disk
#define HID_CONSUMER_ENTER_DISC     187 // Enter Disc
#define HID_CONSUMER_REPEAT         188 // Repeat
#define HID_CONSUMER_STOP_EJECT     204 // Stop/Eject
#define HID_CONSUMER_PLAY_PAUSE     205 // Play/Pause
#define HID_CONSUMER_PLAY_SKIP      206 // Play/Skip

#define HID_CONSUMER_VOLUME         224 // Volume
#define HID_CONSUMER_BALANCE        225 // Balance
#define HID_CONSUMER_MUTE           226 // Mute
#define HID_CONSUMER_BASS           227 // Bass
#define HID_CONSUMER_VOLUME_UP      233 // Volume Increment
#define HID_CONSUMER_VOLUME_DOWN    234 // Volume Decrement

#define HID_RPT_ID_CC_IN        3   // Consumer Control input report ID
#define HID_CC_IN_RPT_LEN       2   // Consumer Control input report Len
void esp_hidd_send_consumer_value(uint8_t key_cmd, bool key_pressed)
{
    uint8_t buffer[HID_CC_IN_RPT_LEN] = {0, 0};
    if (key_pressed) {
        switch (key_cmd) {
        case HID_CONSUMER_CHANNEL_UP:
            HID_CC_RPT_SET_CHANNEL(buffer, HID_CC_RPT_CHANNEL_UP);
            break;

        case HID_CONSUMER_CHANNEL_DOWN:
            HID_CC_RPT_SET_CHANNEL(buffer, HID_CC_RPT_CHANNEL_DOWN);
            break;

        case HID_CONSUMER_VOLUME_UP:
            HID_CC_RPT_SET_VOLUME_UP(buffer);
            break;

        case HID_CONSUMER_VOLUME_DOWN:
            HID_CC_RPT_SET_VOLUME_DOWN(buffer);
            break;

        case HID_CONSUMER_MUTE:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_MUTE);
            break;

        case HID_CONSUMER_POWER:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_POWER);
            break;

        case HID_CONSUMER_RECALL_LAST:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_LAST);
            break;

        case HID_CONSUMER_ASSIGN_SEL:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_ASSIGN_SEL);
            break;

        case HID_CONSUMER_PLAY:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_PLAY);
            break;

        case HID_CONSUMER_PAUSE:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_PAUSE);
            break;

        case HID_CONSUMER_RECORD:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_RECORD);
            break;

        case HID_CONSUMER_FAST_FORWARD:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_FAST_FWD);
            break;

        case HID_CONSUMER_REWIND:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_REWIND);
            break;

        case HID_CONSUMER_SCAN_NEXT_TRK:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_SCAN_NEXT_TRK);
            break;

        case HID_CONSUMER_SCAN_PREV_TRK:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_SCAN_PREV_TRK);
            break;

        case HID_CONSUMER_STOP:
            HID_CC_RPT_SET_BUTTON(buffer, HID_CC_RPT_STOP);
            break;

        default:
            break;
        }
    }
    esp_hidd_dev_input_set(s_ble_hid_param.hid_dev, 0, HID_RPT_ID_CC_IN, buffer, HID_CC_IN_RPT_LEN);
    return;
}

#if !CONFIG_BT_NIMBLE_ENABLED || CONFIG_EXAMPLE_HID_DEVICE_ROLE == 1
void ble_hid_demo_task(void *pvParameters)
{
    static bool send_volum_up = false;
    while (1) {
        ESP_LOGI(TAG, "Send the volume");
        if (send_volum_up) {
            esp_hidd_send_consumer_value(HID_CONSUMER_VOLUME_UP, true);
            vTaskDelay(100 / portTICK_PERIOD_MS);
            esp_hidd_send_consumer_value(HID_CONSUMER_VOLUME_UP, false);
        } else {
            esp_hidd_send_consumer_value(HID_CONSUMER_VOLUME_DOWN, true);
            vTaskDelay(100 / portTICK_PERIOD_MS);
            esp_hidd_send_consumer_value(HID_CONSUMER_VOLUME_DOWN, false);
        }
        send_volum_up = !send_volum_up;
        vTaskDelay(2000 / portTICK_PERIOD_MS);
    }
}
#endif

void ble_hid_task_start_up(void)
{
    if (s_ble_hid_param.task_hdl) {
        // Task already exists
        return;
    }
#if !CONFIG_BT_NIMBLE_ENABLED
    /* Executed for bluedroid */
    xTaskCreate(ble_hid_demo_task, "ble_hid_demo_task", 2 * 1024, NULL, configMAX_PRIORITIES - 3,
                &s_ble_hid_param.task_hdl);
#elif CONFIG_EXAMPLE_HID_DEVICE_ROLE == 1
    xTaskCreate(ble_hid_demo_task, "ble_hid_demo_task", 3 * 1024, NULL, configMAX_PRIORITIES - 3,
                &s_ble_hid_param.task_hdl);

#elif CONFIG_EXAMPLE_HID_DEVICE_ROLE == 2
    /* Nimble Specific */
    xTaskCreate(ble_hid_demo_task_kbd, "ble_hid_demo_task_kbd", 3 * 1024, NULL, configMAX_PRIORITIES - 3,
                &s_ble_hid_param.task_hdl);
#elif CONFIG_EXAMPLE_HID_DEVICE_ROLE == 3
    /* Nimble Specific */
    xTaskCreate(ble_hid_demo_task_mouse, "ble_hid_demo_task_mouse", 3 * 1024, NULL, configMAX_PRIORITIES - 3,
                &s_ble_hid_param.task_hdl);
#endif
}

void ble_hid_task_shut_down(void)
{
    if (s_ble_hid_param.task_hdl) {
        vTaskDelete(s_ble_hid_param.task_hdl);
        s_ble_hid_param.task_hdl = NULL;
    }
}

static void ble_hidd_event_callback(void *handler_args, esp_event_base_t base, int32_t id, void *event_data)
{
    esp_hidd_event_t event = (esp_hidd_event_t)id;
    esp_hidd_event_data_t *param = (esp_hidd_event_data_t *)event_data;
    static const char *TAG = "HID_DEV_BLE";

    switch (event) {
    case ESP_HIDD_START_EVENT: {
        ESP_LOGI(TAG, "START");
        esp_hid_ble_gap_adv_start();
        break;
    }
    case ESP_HIDD_CONNECT_EVENT: {
        ESP_LOGI(TAG, "CONNECT");
        /* USB 供电的外设，固定上报满电，避免 Windows 提示电量不足 */
        esp_hidd_dev_battery_set(s_ble_hid_param.hid_dev, 100);
        s_kvm_offline = 0;
        kvm_led_state(s_kvm_remote);
        break;
    }
    case ESP_HIDD_PROTOCOL_MODE_EVENT: {
        ESP_LOGI(TAG, "PROTOCOL MODE[%u]: %s", param->protocol_mode.map_index, param->protocol_mode.protocol_mode ? "REPORT" : "BOOT");
        break;
    }
    case ESP_HIDD_CONTROL_EVENT: {
        ESP_LOGI(TAG, "CONTROL[%u]: %sSUSPEND", param->control.map_index, param->control.control ? "EXIT_" : "");
        if (param->control.control)
        {
            // exit suspend
            ble_hid_task_start_up();
        } else {
            // suspend
            ble_hid_task_shut_down();
        }
    break;
    }
    case ESP_HIDD_OUTPUT_EVENT: {
        ESP_LOGI(TAG, "OUTPUT[%u]: %8s ID: %2u, Len: %d, Data:", param->output.map_index, esp_hid_usage_str(param->output.usage), param->output.report_id, param->output.length);
        ESP_LOG_BUFFER_HEX(TAG, param->output.data, param->output.length);
        /* 反向通道：笔记本 LED/Output Report -> 串口 AA 08 <rid> <val>
           用 0x08 而非 0x03：0x03 是 BOOT 键切换信号，键盘 LED 变化频繁，
           用同一帧类型会被台式机误判成"按了 BOOT 键"而反复切换 */
        {
            uint8_t rev[4];
            rev[0] = 0xAA;
            rev[1] = 0x08;
            rev[2] = param->output.report_id;
            rev[3] = (param->output.length > 0 && param->output.data)
                     ? param->output.data[0] : 0;
            usb_serial_jtag_write_bytes(rev, sizeof(rev), pdMS_TO_TICKS(5));
        }
        break;
    }
    case ESP_HIDD_FEATURE_EVENT: {
        ESP_LOGI(TAG, "FEATURE[%u]: %8s ID: %2u, Len: %d, Data:", param->feature.map_index, esp_hid_usage_str(param->feature.usage), param->feature.report_id, param->feature.length);
        ESP_LOG_BUFFER_HEX(TAG, param->feature.data, param->feature.length);
        break;
    }
    case ESP_HIDD_DISCONNECT_EVENT: {
        ESP_LOGI(TAG, "DISCONNECT: %s", esp_hid_disconnect_reason_str(esp_hidd_dev_transport_get(param->disconnect.dev), param->disconnect.reason));
        s_kvm_offline = 1;
        kvm_led_set(KVM_LED_COLOR_OFFLINE);   /* 红灯：连接断开 */
        ble_hid_task_shut_down();
        esp_hid_ble_gap_adv_start();
        break;
    }
    case ESP_HIDD_STOP_EVENT: {
        ESP_LOGI(TAG, "STOP");
        break;
    }
    default:
        break;
    }
    return;
}
#endif

#if CONFIG_BT_HID_DEVICE_ENABLED
static local_param_t s_bt_hid_param = {0};
const unsigned char mouseReportMap[] = {
    0x05, 0x01,                    // USAGE_PAGE (Generic Desktop)
    0x09, 0x02,                    // USAGE (Mouse)
    0xa1, 0x01,                    // COLLECTION (Application)

    0x09, 0x01,                    //   USAGE (Pointer)
    0xa1, 0x00,                    //   COLLECTION (Physical)

    0x05, 0x09,                    //     USAGE_PAGE (Button)
    0x19, 0x01,                    //     USAGE_MINIMUM (Button 1)
    0x29, 0x03,                    //     USAGE_MAXIMUM (Button 3)
    0x15, 0x00,                    //     LOGICAL_MINIMUM (0)
    0x25, 0x01,                    //     LOGICAL_MAXIMUM (1)
    0x95, 0x03,                    //     REPORT_COUNT (3)
    0x75, 0x01,                    //     REPORT_SIZE (1)
    0x81, 0x02,                    //     INPUT (Data,Var,Abs)
    0x95, 0x01,                    //     REPORT_COUNT (1)
    0x75, 0x05,                    //     REPORT_SIZE (5)
    0x81, 0x03,                    //     INPUT (Cnst,Var,Abs)

    0x05, 0x01,                    //     USAGE_PAGE (Generic Desktop)
    0x09, 0x30,                    //     USAGE (X)
    0x09, 0x31,                    //     USAGE (Y)
    0x09, 0x38,                    //     USAGE (Wheel)
    0x15, 0x81,                    //     LOGICAL_MINIMUM (-127)
    0x25, 0x7f,                    //     LOGICAL_MAXIMUM (127)
    0x75, 0x08,                    //     REPORT_SIZE (8)
    0x95, 0x03,                    //     REPORT_COUNT (3)
    0x81, 0x06,                    //     INPUT (Data,Var,Rel)

    0xc0,                          //   END_COLLECTION
    0xc0                           // END_COLLECTION
};

static esp_hid_raw_report_map_t bt_report_maps[] = {
    {
        .data = mouseReportMap,
        .len = sizeof(mouseReportMap)
    },
};

static esp_hid_device_config_t bt_hid_config = {
    .vendor_id          = 0x16C0,
    .product_id         = 0x05DF,
    .version            = 0x0100,
    .device_name        = "ESP BT HID1",
    .manufacturer_name  = "Espressif",
    .serial_number      = "1234567890",
    .report_maps        = bt_report_maps,
    .report_maps_len    = 1
};

// send the buttons, change in x, and change in y
void send_mouse(uint8_t buttons, char dx, char dy, char wheel)
{
    static uint8_t buffer[4] = {0};
    buffer[0] = buttons;
    buffer[1] = dx;
    buffer[2] = dy;
    buffer[3] = wheel;
    esp_hidd_dev_input_set(s_bt_hid_param.hid_dev, 0, 0, buffer, 4);
}

void bt_hid_demo_task(void *pvParameters)
{
    static const char* help_string = "########################################################################\n"\
    "BT hid mouse demo usage:\n"\
    "You can input these value to simulate mouse: 'q', 'w', 'e', 'a', 's', 'd', 'h'\n"\
    "q -- click the left key\n"\
    "w -- move up\n"\
    "e -- click the right key\n"\
    "a -- move left\n"\
    "s -- move down\n"\
    "d -- move right\n"\
    "h -- show the help\n"\
    "########################################################################\n";
    printf("%s\n", help_string);
    char c;
    while (1) {
        c = fgetc(stdin);
        switch (c) {
        case 'q':
            send_mouse(1, 0, 0, 0);
            break;
        case 'w':
            send_mouse(0, 0, -10, 0);
            break;
        case 'e':
            send_mouse(2, 0, 0, 0);
            break;
        case 'a':
            send_mouse(0, -10, 0, 0);
            break;
        case 's':
            send_mouse(0, 0, 10, 0);
            break;
        case 'd':
            send_mouse(0, 10, 0, 0);
            break;
        case 'h':
            printf("%s\n", help_string);
            break;
        default:
            break;
        }
        vTaskDelay(10 / portTICK_PERIOD_MS);
    }
}

void bt_hid_task_start_up(void)
{
    xTaskCreate(bt_hid_demo_task, "bt_hid_demo_task", 2 * 1024, NULL, configMAX_PRIORITIES - 3, &s_bt_hid_param.task_hdl);
    return;
}

void bt_hid_task_shut_down(void)
{
    if (s_bt_hid_param.task_hdl) {
        vTaskDelete(s_bt_hid_param.task_hdl);
        s_bt_hid_param.task_hdl = NULL;
    }
}

static void bt_hidd_event_callback(void *handler_args, esp_event_base_t base, int32_t id, void *event_data)
{
    esp_hidd_event_t event = (esp_hidd_event_t)id;
    esp_hidd_event_data_t *param = (esp_hidd_event_data_t *)event_data;
    static const char *TAG = "HID_DEV_BT";

    switch (event) {
    case ESP_HIDD_START_EVENT: {
        if (param->start.status == ESP_OK) {
            ESP_LOGI(TAG, "START OK");
            ESP_LOGI(TAG, "Setting to connectable, discoverable");
            esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
        } else {
            ESP_LOGE(TAG, "START failed!");
        }
        break;
    }
    case ESP_HIDD_CONNECT_EVENT: {
        if (param->connect.status == ESP_OK) {
            ESP_LOGI(TAG, "CONNECT OK");
            ESP_LOGI(TAG, "Setting to non-connectable, non-discoverable");
            esp_bt_gap_set_scan_mode(ESP_BT_NON_CONNECTABLE, ESP_BT_NON_DISCOVERABLE);
            bt_hid_task_start_up();
        } else {
            ESP_LOGE(TAG, "CONNECT failed!");
        }
        break;
    }
    case ESP_HIDD_PROTOCOL_MODE_EVENT: {
        ESP_LOGI(TAG, "PROTOCOL MODE[%u]: %s", param->protocol_mode.map_index, param->protocol_mode.protocol_mode ? "REPORT" : "BOOT");
        break;
    }
    case ESP_HIDD_OUTPUT_EVENT: {
        ESP_LOGI(TAG, "OUTPUT[%u]: %8s ID: %2u, Len: %d, Data:", param->output.map_index, esp_hid_usage_str(param->output.usage), param->output.report_id, param->output.length);
        ESP_LOG_BUFFER_HEX(TAG, param->output.data, param->output.length);
        break;
    }
    case ESP_HIDD_FEATURE_EVENT: {
        ESP_LOGI(TAG, "FEATURE[%u]: %8s ID: %2u, Len: %d, Data:", param->feature.map_index, esp_hid_usage_str(param->feature.usage), param->feature.report_id, param->feature.length);
        ESP_LOG_BUFFER_HEX(TAG, param->feature.data, param->feature.length);
        break;
    }
    case ESP_HIDD_DISCONNECT_EVENT: {
        if (param->disconnect.status == ESP_OK) {
            ESP_LOGI(TAG, "DISCONNECT OK");
            bt_hid_task_shut_down();
            ESP_LOGI(TAG, "Setting to connectable, discoverable again");
            esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
        } else {
            ESP_LOGE(TAG, "DISCONNECT failed!");
        }
        break;
    }
    case ESP_HIDD_STOP_EVENT: {
        ESP_LOGI(TAG, "STOP");
        break;
    }
    default:
        break;
    }
    return;
}

#if CONFIG_BT_SDP_COMMON_ENABLED
static void esp_sdp_cb(esp_sdp_cb_event_t event, esp_sdp_cb_param_t *param)
{
    switch (event) {
    case ESP_SDP_INIT_EVT:
        ESP_LOGI(TAG, "ESP_SDP_INIT_EVT: status:%d", param->init.status);
        if (param->init.status == ESP_SDP_SUCCESS) {
            esp_bluetooth_sdp_dip_record_t dip_record = {
                .hdr =
                    {
                        .type = ESP_SDP_TYPE_DIP_SERVER,
                    },
                .vendor           = bt_hid_config.vendor_id,
                .vendor_id_source = ESP_SDP_VENDOR_ID_SRC_BT,
                .product          = bt_hid_config.product_id,
                .version          = bt_hid_config.version,
                .primary_record   = true,
            };
            esp_sdp_create_record((esp_bluetooth_sdp_record_t *)&dip_record);
        }
        break;
    case ESP_SDP_DEINIT_EVT:
        ESP_LOGI(TAG, "ESP_SDP_DEINIT_EVT: status:%d", param->deinit.status);
        break;
    case ESP_SDP_SEARCH_COMP_EVT:
        ESP_LOGI(TAG, "ESP_SDP_SEARCH_COMP_EVT: status:%d", param->search.status);
        break;
    case ESP_SDP_CREATE_RECORD_COMP_EVT:
        ESP_LOGI(TAG, "ESP_SDP_CREATE_RECORD_COMP_EVT: status:%d, handle:0x%x", param->create_record.status,
                 param->create_record.record_handle);
        break;
    case ESP_SDP_REMOVE_RECORD_COMP_EVT:
        ESP_LOGI(TAG, "ESP_SDP_REMOVE_RECORD_COMP_EVT: status:%d", param->remove_record.status);
        break;
    default:
        break;
    }
}
#endif /* CONFIG_BT_SDP_COMMON_ENABLED */

#endif

#if CONFIG_BT_NIMBLE_ENABLED
void ble_hid_device_host_task(void *param)
{
    ESP_LOGI(TAG, "BLE Host Task Started");
    /* This function will return only when nimble_port_stop() is executed */
    nimble_port_run();

    nimble_port_freertos_deinit();
}
void ble_store_config_init(void);
#endif

void app_main(void)
{
    esp_err_t ret;

    kvm_led_init();
    kvm_led_set(KVM_LED_COLOR_OFFLINE);   /* 上电默认无连接：红灯，等 CONNECT 再变 */
    s_last_activity = xTaskGetTickCount();
    xTaskCreate(kvm_led_task, "kvm_led", 3072, NULL, 3, NULL);
    xTaskCreate(kvm_boot_btn_task, "kvm_btn", 2560, NULL, 4, NULL);

#if HID_DEV_MODE == HIDD_IDLE_MODE
    ESP_LOGE(TAG, "Please turn on BT HID device or BLE!");
    return;
#endif
    ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK( ret );

    ESP_LOGI(TAG, "setting hid gap, mode:%d", HID_DEV_MODE);
    ret = esp_hid_gap_init(HID_DEV_MODE);
    ESP_ERROR_CHECK( ret );

#if CONFIG_BT_BLE_ENABLED || CONFIG_BT_NIMBLE_ENABLED
#if CONFIG_EXAMPLE_HID_DEVICE_ROLE == 2
    ret = esp_hid_ble_gap_adv_init(ESP_HID_APPEARANCE_KEYBOARD, ble_hid_config.device_name);
#elif CONFIG_EXAMPLE_HID_DEVICE_ROLE == 3
    ret = esp_hid_ble_gap_adv_init(ESP_HID_APPEARANCE_MOUSE, ble_hid_config.device_name);
#else
    ret = esp_hid_ble_gap_adv_init(ESP_HID_APPEARANCE_GENERIC, ble_hid_config.device_name);
#endif
    ESP_ERROR_CHECK( ret );
#if CONFIG_BT_BLE_ENABLED
    if ((ret = esp_ble_gatts_register_callback(esp_hidd_gatts_event_handler)) != ESP_OK) {
        ESP_LOGE(TAG, "GATTS register callback failed: %d", ret);
        return;
    }
#endif
    ESP_LOGI(TAG, "setting ble device");
    ESP_ERROR_CHECK(
        esp_hidd_dev_init(&ble_hid_config, ESP_HID_TRANSPORT_BLE, ble_hidd_event_callback, &s_ble_hid_param.hid_dev));
#endif

#if CONFIG_BT_HID_DEVICE_ENABLED
    ESP_LOGI(TAG, "setting device name");
    esp_bt_gap_set_device_name(bt_hid_config.device_name);
    ESP_LOGI(TAG, "setting cod major, peripheral");
    esp_bt_cod_t cod = {0};
    cod.major = ESP_BT_COD_MAJOR_DEV_PERIPHERAL;
    cod.minor = ESP_BT_COD_MINOR_PERIPHERAL_POINTING;
    esp_bt_gap_set_cod(cod, ESP_BT_SET_COD_MAJOR_MINOR);
    vTaskDelay(1000 / portTICK_PERIOD_MS);
    ESP_LOGI(TAG, "setting bt device");
    ESP_ERROR_CHECK(
        esp_hidd_dev_init(&bt_hid_config, ESP_HID_TRANSPORT_BT, bt_hidd_event_callback, &s_bt_hid_param.hid_dev));
#if CONFIG_BT_SDP_COMMON_ENABLED
    ESP_ERROR_CHECK(esp_sdp_register_callback(esp_sdp_cb));
    ESP_ERROR_CHECK(esp_sdp_init());
#endif /* CONFIG_BT_SDP_COMMON_ENABLED */
#endif /* CONFIG_BT_HID_DEVICE_ENABLED */
#if CONFIG_BT_NIMBLE_ENABLED
    /* XXX Need to have template for store */
    ble_store_config_init();

    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;
	/* Starting nimble task after gatts is initialized*/
    ret = esp_nimble_enable(ble_hid_device_host_task);
    if (ret) {
        ESP_LOGE(TAG, "esp_nimble_enable failed: %d", ret);
    }
#endif
}
