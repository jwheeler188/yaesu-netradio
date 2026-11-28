"""APRS packet reading (ft897_aprs.py), no radio or Dire Wolf needed: python3 pi/tests/test_aprs_parse.py
Mic-E cases were checked against Dire Wolf's own decode_aprs."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import ft897_aprs as a
ok = bad = 0
def t(name, c, info=""):
    global ok, bad
    ok += bool(c); bad += not c; print(("pass " if c else "FAIL ") + name + ("" if c else f"   {info}"))
P = lambda line: a.parse(a.parse_tnc2(line))
near = lambda x, y: abs(x - y) < 1e-3
p = P("N0CALL>APRS,WIDE2-1:!4903.50N/07201.75W-Test 1"); t("plain position", p["kind"] == "position" and near(p["lat"], 49.0583) and near(p["lon"], -72.0292) and p["table"] + p["sym"] == "/-" and p["comment"] == "Test 1", p)
p = P("N0CALL>APRS:=/5L!!<*e7>7P["); t("compressed position", near(p["lat"], 49.5) and near(p["lon"], -72.75), p)
p = P('N0CALL>S32U6T,WIDE1-1:`(_fn"Oj/>x'); t("Mic-E (decode_aprs: N 33 25.64, W 012 07.74, course 251)", near(p["lat"], 33.4273) and near(p["lon"], -12.129) and p["course"] == 251, p)
p = P("N0CALL>SYSXWU:`pUNl<b>/x"); t("Mic-E (decode_aprs: N 39 38.75, W 104 57.50, course 270)", near(p["lat"], 39.6458) and near(p["lon"], -104.9583) and p["course"] == 270, p)
p = P("N0CALL>APRS:!3939.00N[10500.00W/185/000/A=005280Somewhere"); t("an invalid symbol table: position kept, symbol flagged", "sym" not in p and p["sym_bad"] == "[/" and near(p["lat"], 39.65) and p["course"] == 185, p)
t("altitude taken out of the comment", p["alt_ft"] == 5280 and p["comment"] == "Somewhere", p)
p = P("N0CALL>APRS:!3939.00NS10500.00W#overlay"); t("overlay symbol kept", p["table"] == "S" and p["sym"] == "#", p)
p = P("N0CALL>APRS::W5PL-10  :Hello{12"); t("message with number", p["kind"] == "message" and p["to"] == "W5PL-10" and p["text"] == "Hello" and p["msgno"] == "12", p)
p = P("N0CALL>APRS::W5PL-10  :ack12"); t("ack", p["kind"] == "ack" and p["msgno"] == "12", p)
p = P("N0CALL>APRS::BLN1     :Net at 8"); t("bulletin", p["kind"] == "bulletin", p)
p = P("N0CALL>APRS:;SHELTER  *092345z4903.50N/07201.75WhOpen"); t("object", p["kind"] == "object" and p["name"] == "SHELTER" and p["alive"], p)
p = P("N0CALL>APRS:)AID #2!4903.50N/07201.75WA"); t("item", p["kind"] == "item" and p["name"] == "AID #2", p)
p = P("GATE>APRS,TCPIP*:}N0CALL>APRS,TCPIP,GATE*:!4903.50N/07201.75W-inner"); t("third-party (gated) packet", p["src"] == "N0CALL" and p["via3"] == "GATE" and p["kind"] == "position", p)
p = P("N0CALL>APRS:>Net tonight"); t("status", p["kind"] == "status" and p["text"] == "Net tonight", p)
t("garbage never raises", P("N0CALL>APRS:!xx")["kind"] in ("other", "position") and P("N0CALL>APRS:`")["kind"] == "other")
p = P("N0CALL>APRS:@092345z3939.00N/10500.00W_220/004g005t077r001p012P003h50b10132L540wDavis")
t("weather station: wind, temperature, rain, humidity, pressure (not course/speed)", p["kind"] == "weather" and "course" not in p and p["wx"] == {"wind_dir": 220, "wind_mph": 4, "gust_mph": 5, "temp_f": 77, "rain_1h_in": .01, "rain_24h_in": .12, "rain_mid_in": .03, "humidity": 50, "pressure_mb": 1013.2, "lum_wm2": 540} and p["comment"] == "wDavis", p)
p = P("N0CALL>APRS:!3939.00N/10500.00W_.../...g...t-05r...h00b09870"); t("weather: missing values, below zero, humidity 00 = 100 %", p["wx"] == {"temp_f": -5, "humidity": 100, "pressure_mb": 987.0}, p)
p = P("N0CALL>APRS:_10090556c220s004g005t077r000p000P000h50b09900wRSW"); t("weather without a position", p["kind"] == "weather" and p["wx"]["wind_dir"] == 220 and p["wx"]["wind_mph"] == 4 and p["wx"]["temp_f"] == 77, p)
p = P("N0CALL>APRS:!3939.00N/10500.00W>185/030 car"); t("a car still has course and speed", p["course"] == 185 and "wx" not in p, p)
p = P("N0CALL-9>APT314,WIDE2-1:/224310h3939.00N/10500.00W'195/058/A=000495|\"T%j'p|!w/c!")
t("tracker extensions: DAO adds precision, telemetry and DAO leave the comment", near(p["lat"], 39.65) and p["lat"] > 39.65 and p["comment"] == "" and p["telemetry"] == '|"T%j\'p|' and p["course"] == 195 and p["alt_ft"] == 495, p)
p = P("N0CALL-9>APT314,WIDE2-1:/224310k3939.00N/10500.00W'795/058"); t("an impossible course (damaged packet) is dropped", "course" not in p and "speed" not in p, p)
p = P("N0CALL>APRS:!3939.00N/10500.00W-Hi !W53! there"); t("DAO in human-readable form", abs(p["lat"] - (39.65 + .005 / 60)) < 1e-7 and p["comment"] == "Hi there", p)
p = P("N0CALL>APRS:@272243z3939.00N/10500.00W_207/002g004t063r000p000P000h46b10199.DsVP"); t("weather station type code is not a comment", p["wx_unit"] == "DsVP" and p["comment"] == "", p)
f = a.ax25_encode("W5PL-10", "APZYNR", ["WIDE2-1"], a.position_info(39.65, -105.0, "/", "-", "hi")); d = a.ax25_decode(f)
t("frame round trip", a.tnc2(d) == "W5PL-10>APZYNR,WIDE2-1:=3939.00N/10500.00W-hi" and a.KissReader().feed(a.kiss_wrap(f))[0] == f, a.tnc2(d))
t("grid squares", a.grid_to_latlon("DM79mp") == (39.64583, -104.95833) and a.latlon_to_grid(39.65, -105.0) == "DM79mp")
t("message text cleaned", a.message_info("n0call", "a|b~c{d", 5) == ":N0CALL   :a b c d{5")
print(f"{ok} passed, {bad} failed")
