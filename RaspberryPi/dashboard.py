import serial
import pymysql
import threading
import datetime
import time
from queue import Queue
from flask import Flask, render_template, request

arduino = serial.Serial("/dev/ttyACM0", 9600, timeout=1)
arduino.setDTR(False)

app = Flask(__name__)

current_thresholds = None  # [t_low, t_high, h_low, h_high]
command_queue = Queue()

enable_collect_log = False
enable_request_log = True

def log_collect(s: str):
    if enable_collect_log:
        print(s)

def log_request(s: str):
    if enable_request_log:
        print(s)

def serial_worker(dbconn):
    global current_thresholds

    command_queue.put("GET_THRESH\n")

    while True:
        # send pending command
        try:
            cmd = command_queue.get_nowait()
        except:
            cmd = None

        if cmd:
            arduino.write(cmd.encode())
            log_collect(f"Sent command: {cmd.strip()}")

        # read line
        try:
            line = arduino.readline().decode().strip()
        except serial.serialutil.SerialException:
            line = ""

        if not line:
            time.sleep(0.01)
            continue

        log_collect(f"Serial line: {line}")

        if line.startswith("THRESH,"):
            parts = line.split(",")
            if len(parts) != 5:
                continue
            try:
                t_low  = int(float(parts[1]))
                t_high = int(float(parts[2]))
                h_low  = int(float(parts[3]))
                h_high = int(float(parts[4]))
            except ValueError:
                continue
            current_thresholds = [t_low, t_high, h_low, h_high]
            log_collect(f"Updated thresholds from Arduino: {current_thresholds}")
            continue

        if line.startswith("DATA,"):
            parts = line.split(",")
            if len(parts) != 4:
                continue
            try:
                temp = float(parts[1])
                hum  = float(parts[2])
                d_int = int(parts[3])
            except ValueError:
                continue

            DANGER_LEVELS = ("unlikely", "possible", "likely")
            if 0 <= d_int < len(DANGER_LEVELS):
                danger = DANGER_LEVELS[d_int]
            else:
                danger = "possible"

            cursor = dbconn.cursor()
            cursor.execute(
                "INSERT INTO dangerlog "
                "(recordedTime, recordedHumidity, recordedTemp, recordedDanger) "
                "VALUES (%s, %s, %s, %s)",
                (datetime.datetime.now(), hum, temp, danger),
            )
            dbconn.commit()
            cursor.close()
            log_collect(f"Logged to DB: temp={temp}, hum={hum}, danger={danger}")
            continue

        # ignore unknown lines

def danger_where_clauses(temp_low, temp_high, hum_low, hum_high):
    # original logic:
    # UNLIKELY: humidity > high_h OR temp < low_t
    # LIKELY:   humidity < low_h AND temp > high_t
    # POSSIBLE: everything else

    unlikely_clause = (
        f"(recordedHumidity > {hum_high} OR recordedTemp < {temp_low})"
    )
    likely_clause = (
        f"(recordedHumidity < {hum_low} AND recordedTemp > {temp_high})"
    )
    possible_clause = f"NOT ({unlikely_clause} OR {likely_clause})"

    return unlikely_clause, possible_clause, likely_clause

@app.route("/", methods=["GET", "POST"])
def index():
    global current_thresholds

    date_clause = "1"
    mode = "display"

    if current_thresholds is None:
        t_low, t_high, h_low, h_high = 27, 27, 47, 50
    else:
        t_low, t_high, h_low, h_high = current_thresholds

    if request.method == "POST":
        start = request.form.get("start") or ""
        end = request.form.get("end") or ""
        mode = request.form.get("recalculate") or "display"
        log_request(f"{start=} {end=} {mode=}")

        if start == "":
            if end == "":
                date_clause = "1"
            else:
                date_clause = f"recordedTime < '{end} 0:00:00'"
        else:
            if end == "":
                date_clause = f"recordedTime > '{start} 0:00:00'"
            else:
                date_clause = f"recordedTime BETWEEN '{start} 0:00:00' AND '{end} 0:00:00'"

        t_low  = int(float(request.form.get("temp-low")))
        t_high = int(float(request.form.get("temp-high")))
        h_low  = int(float(request.form.get("hum-low")))
        h_high = int(float(request.form.get("hum-high")))
        log_request(f"{t_low=} {t_high=} {h_low=} {h_high=}")

        if mode in ("update", "both"):
            cmd = f"SET_THRESH,{t_low},{t_high},{h_low},{h_high}\n"
            command_queue.put(cmd)

        current_thresholds = [t_low, t_high, h_low, h_high]
        
    recompute = mode in ("display", "both")

    cursor = dbconn.cursor()

    # most recent "likely"
    if not recompute:
        cursor.execute(
            f"SELECT * FROM dangerlog "
            f"WHERE {date_clause} AND recordedDanger = 'likely' "
            f"ORDER BY recordedTime DESC LIMIT 1;"
        )
    else:
        unlikely_clause, possible_clause, likely_clause = danger_where_clauses(
            t_low, t_high, h_low, h_high
        )
        cursor.execute(
            f"SELECT * FROM dangerlog "
            f"WHERE {date_clause} AND {likely_clause} "
            f"ORDER BY recordedTime DESC LIMIT 1;"
        )

    most_recent_high_list = cursor.fetchall()
    if len(most_recent_high_list) == 0:
        most_recent_high = "No high dangers recorded."
    else:
        most_recent_high = most_recent_high_list[0]
    
    # most recent
    cursor.execute(
        f"SELECT * FROM dangerlog "
        f"WHERE {date_clause} "
        f"ORDER BY recordedTime DESC LIMIT 1;"
    )

    most_recent_list = cursor.fetchall()
    if len(most_recent_list) == 0:
        most_recent = "No high dangers recorded."
    else:
        most_recent = most_recent_list[0]

    # counts for pie chart
    if not recompute:
        cursor.execute(
            f"SELECT COUNT(*) FROM dangerlog WHERE {date_clause} AND recordedDanger = 'likely';"
        )
        high_count = cursor.fetchall()[0][0]

        cursor.execute(
            f"SELECT COUNT(*) FROM dangerlog WHERE {date_clause} AND recordedDanger = 'possible';"
        )
        med_count = cursor.fetchall()[0][0]

        cursor.execute(
            f"SELECT COUNT(*) FROM dangerlog WHERE {date_clause} AND recordedDanger = 'unlikely';"
        )
        low_count = cursor.fetchall()[0][0]
    else:
        unlikely_clause, possible_clause, likely_clause = danger_where_clauses(
            t_low, t_high, h_low, h_high
        )

        cursor.execute(
            f"SELECT COUNT(*) FROM dangerlog WHERE {date_clause} AND {likely_clause};"
        )
        high_count = cursor.fetchall()[0][0]

        cursor.execute(
            f"SELECT COUNT(*) FROM dangerlog WHERE {date_clause} AND {possible_clause};"
        )
        med_count = cursor.fetchall()[0][0]

        cursor.execute(
            f"SELECT COUNT(*) FROM dangerlog WHERE {date_clause} AND {unlikely_clause};"
        )
        low_count = cursor.fetchall()[0][0]

    cursor.close()

    total = high_count + med_count + low_count
    if total == 0:
        total = 1

    return render_template(
        "index.html",
        high_perc=high_count * 100 / total,
        med_perc=med_count * 100 / total,
        low_perc=low_count * 100 / total,
        most_recent_high=most_recent_high,
        low_t_thresh=t_low,
        high_t_thresh=t_high,
        low_h_thresh=h_low,
        high_h_thresh=h_high,
        most_recent=most_recent
    )

if __name__ == "__main__":
    dbconn = pymysql.connect(
        host="localhost",
        user="pi",
        password="",
        database="fire_danger_db",
    )

    t = threading.Thread(target=serial_worker, args=(dbconn,), daemon=True)
    t.start()

    app.run(debug=True, host="localhost", port=8080)
