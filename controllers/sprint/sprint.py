
import datetime
import os
import subprocess
from pathlib import Path
import json
from controller import Supervisor, AnsiCodes, Node
import time
from lib.utils import ElsirosConfigInterface, get_logger

supervisor = Supervisor()
time_step = int(supervisor.getBasicTimeStep())

robot_translation = supervisor.getFromDef('BLUE_PLAYER_1').getField('translation')

current_working_directory = Path.cwd()

logger = get_logger(str(current_working_directory) + "\Sprint_log.txt")

def uprint(*text):
    logger.write(" ".join(list(map(str, text))) + "\n")

os.chdir(current_working_directory.parent/'Robofest_TEAM')

role01 = 'sprint' 
second_pressed_button = '4'
initial_coord = '[0.0, 0, 0]'
robot_color = 'blue'
robot_number = '1'
team_id = '-1'          # value -1 means game will be playing without Game Controller
port01 = '7001'
filename01 = "output" + f"{port01}"+ ".txt"
with open(filename01, "w") as f01:
    print(datetime.datetime.now(), file = f01)
    p01 = subprocess.Popen(['python', 'main_pb.py', port01, team_id, robot_color, robot_number, role01, second_pressed_button, initial_coord], stderr=f01)

distance_count = 0

elsiros = ElsirosConfigInterface(supervisor)
elsiros.enable_recording()

while supervisor.step(time_step) != -1 :
    #message = 'robot position: ' + str(robot_translation.getSFVec3f()) + 'step: ' + str(supervisor.step(time_step))
    #print(message)
    distance_count += 1
    y_coordinate = robot_translation.getSFVec3f()[1]
    if y_coordinate > 0.5 or y_coordinate< -0.5:
        uprint(datetime.datetime.now(), 'distance was NOT finished due to failure ')
        break
    if robot_translation.getSFVec3f()[0] > 3:
        uprint(datetime.datetime.now(), 'distance was finished within timesteps: ', distance_count)
        break

p01.terminate()
supervisor.step(time_step)
elsiros.stop_recording()
supervisor.simulationQuit(0)    
logger.close()