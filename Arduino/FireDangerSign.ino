#include <dht_nonblocking.h>
#include <Servo.h>

constexpr uint8_t DHT11_PIN         = 2;
constexpr uint8_t PHOTORESISTOR_PIN = A5;
constexpr uint8_t SERVO_PIN         = 9;
constexpr uint8_t RED_PIN           = 4;
constexpr uint8_t YELLOW_PIN        = 5;
constexpr uint8_t GREEN_PIN         = 6;

Servo g_servo;
DHT_nonblocking g_dht_sensor{DHT11_PIN, DHT_TYPE_11};

float g_temp_low  = 27.f;
float g_temp_high = 27.f;
float g_hum_low   = 47.f;
float g_hum_high  = 50.f;

enum class DangerLevel { UNLIKELY = 0, POSSIBLE = 1, LIKELY = 2 };

DangerLevel calculate_danger(float temp, float humidity)
{
  if (humidity > g_hum_high || temp < g_temp_low)
    return DangerLevel::UNLIKELY;

  if (humidity < g_hum_low && temp > g_temp_high)
    return DangerLevel::LIKELY;

  return DangerLevel::POSSIBLE;
}

void update_servo_position(DangerLevel danger)
{
  switch (danger)
  {
    case DangerLevel::UNLIKELY: g_servo.write(0);   break;
    case DangerLevel::POSSIBLE: g_servo.write(90);  break;
    case DangerLevel::LIKELY:   g_servo.write(180); break;
  }
}

void update_lights(DangerLevel danger, int light_level)
{
  if (light_level < 50)
  {
    switch (danger)
    {
      case DangerLevel::UNLIKELY:
        digitalWrite(GREEN_PIN,  HIGH);
        digitalWrite(RED_PIN,    LOW);
        digitalWrite(YELLOW_PIN, LOW);
        break;
      case DangerLevel::POSSIBLE:
        digitalWrite(YELLOW_PIN, HIGH);
        digitalWrite(GREEN_PIN,  LOW);
        digitalWrite(RED_PIN,    LOW);
        break;
      case DangerLevel::LIKELY:
        digitalWrite(RED_PIN,    HIGH);
        digitalWrite(GREEN_PIN,  LOW);
        digitalWrite(YELLOW_PIN, LOW);
        break;
    }
  }
  else
  {
    digitalWrite(RED_PIN,    LOW);
    digitalWrite(YELLOW_PIN, LOW);
    digitalWrite(GREEN_PIN,  LOW);
  }
}

void print_thresholds()
{
  Serial.print("THRESH,");
  Serial.print(g_temp_low);
  Serial.print(',');
  Serial.print(g_temp_high);
  Serial.print(',');
  Serial.print(g_hum_low);
  Serial.print(',');
  Serial.println(g_hum_high);
}

void apply_thresholds(const char* buffer)
{
  // buffer: "SET_THRESH,t_low,t_high,h_low,h_high"
  float vals[4];
  int idx = 0;

  const char* payload = strchr(buffer, ',');
  if (!payload) return;
  payload++;

  char* copy = strdup(payload);
  char* token = strtok(copy, ",");

  while (token && idx < 4)
  {
    vals[idx++] = atof(token);
    token = strtok(NULL, ",");
  }

  free(copy);

  if (idx == 4)
  {
    g_temp_low  = vals[0];
    g_temp_high = vals[1];
    g_hum_low   = vals[2];
    g_hum_high  = vals[3];
    print_thresholds();
  }
}

void handle_serial()
{
  if (!Serial.available())
    return;

  char buffer[64];
  int n = Serial.readBytesUntil('\n', buffer, sizeof(buffer) - 1);
  if (n <= 0) return;
  buffer[n] = '\0';

  if (strncmp(buffer, "GET_THRESH", 10) == 0)
  {
    print_thresholds();
  }
  else if (strncmp(buffer, "SET_THRESH", 10) == 0)
  {
    apply_thresholds(buffer);
  }
}

void setup()
{
  Serial.begin(9600);

  g_servo.attach(SERVO_PIN);

  pinMode(RED_PIN,    OUTPUT);
  pinMode(YELLOW_PIN, OUTPUT);
  pinMode(GREEN_PIN,  OUTPUT);
}

void loop()
{
  handle_serial();

  float temp;
  float humidity;
  if (!g_dht_sensor.measure(&temp, &humidity))
    return;

  DangerLevel danger = calculate_danger(temp, humidity);

  update_servo_position(danger);
  update_lights(danger, analogRead(PHOTORESISTOR_PIN));

  Serial.print("DATA,");
  Serial.print(temp);
  Serial.print(',');
  Serial.print(humidity);
  Serial.print(',');
  Serial.println(static_cast<int>(danger));
}
