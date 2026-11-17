# Fixture: motor direction on an expander pin (TEST DATA, not the robot's wiring)

| Signal | `pins.h` name | Direction (ESP32 side) | Level shifting | ESP32 GPIO | Strapping pin OK? (yes) | Checked by / date |
|---|---|---|---|---|---|---|
| Left motors PWM | `PIN_MOTOR_LEFT_PWM` | out | – | 25 | | Test Person 2027-01-05 |
| Left motors direction A | `PIN_MOTOR_LEFT_DIR_A` | out | – | X4 | | Test Person 2027-01-05 |
| Left motors direction B | `PIN_MOTOR_LEFT_DIR_B` | out | – | 27 | | Test Person 2027-01-05 |
| Right motors PWM | `PIN_MOTOR_RIGHT_PWM` | out | – | 33 | | Test Person 2027-01-05 |
| Right motors direction A | `PIN_MOTOR_RIGHT_DIR_A` | out | – | 32 | | Test Person 2027-01-05 |
| Right motors direction B | `PIN_MOTOR_RIGHT_DIR_B` | out | – | 23 | | Test Person 2027-01-05 |
| Encoder front-left A | `PIN_ENC_FL_A` | in | 5 V → 3.3 V | 34 | | Test Person 2027-01-05 |
| Encoder front-left B | `PIN_ENC_FL_B` | in | 5 V → 3.3 V | 35 | | Test Person 2027-01-05 |
| Encoder rear-left A | `PIN_ENC_RL_A` | in | 5 V → 3.3 V | 36 | | Test Person 2027-01-05 |
| Encoder rear-left B | `PIN_ENC_RL_B` | in | 5 V → 3.3 V | 39 | | Test Person 2027-01-05 |
| Encoder front-right A | `PIN_ENC_FR_A` | in | 5 V → 3.3 V | 16 | | Test Person 2027-01-05 |
| Encoder front-right B | `PIN_ENC_FR_B` | in | 5 V → 3.3 V | 17 | | Test Person 2027-01-05 |
| Encoder rear-right A | `PIN_ENC_RR_A` | in | 5 V → 3.3 V | 18 | | Test Person 2027-01-05 |
| Encoder rear-right B | `PIN_ENC_RR_B` | in | 5 V → 3.3 V | 19 | | Test Person 2027-01-05 |
| I2C data | `PIN_I2C_SDA` | bidirectional | – | 21 | | Test Person 2027-01-05 |
| I2C clock | `PIN_I2C_SCL` | bidirectional | – | 22 | | Test Person 2027-01-05 |
| Ultrasonic front-left trigger | `PIN_US_FL_TRIG` | out | – | X0 | | Test Person 2027-01-05 |
| Ultrasonic front-left echo | `PIN_US_FL_ECHO` | in | divider | 14 | | Test Person 2027-01-05 |
| Ultrasonic front-centre trigger | `PIN_US_FC_TRIG` | out | – | X1 | | Test Person 2027-01-05 |
| Ultrasonic front-centre echo | `PIN_US_FC_ECHO` | in | divider | 4 | | Test Person 2027-01-05 |
| Ultrasonic front-right trigger | `PIN_US_FR_TRIG` | out | – | X2 | | Test Person 2027-01-05 |
| Ultrasonic front-right echo | `PIN_US_FR_ECHO` | in | divider | 5 | yes | Test Person 2027-01-05 |
| Ultrasonic rear trigger | `PIN_US_REAR_TRIG` | out | – | X3 | | Test Person 2027-01-05 |
| Ultrasonic rear echo | `PIN_US_REAR_ECHO` | in | divider | 15 | yes | Test Person 2027-01-06 |
| Pan servo signal | `PIN_SERVO_PAN` | out | – | 13 | | Test Person 2027-01-05 |
| I2C expander (MCP23017) address | `SCR_EXPANDER_I2C_ADDR` | – | – | 0x20 | | Test Person 2027-01-05 |
