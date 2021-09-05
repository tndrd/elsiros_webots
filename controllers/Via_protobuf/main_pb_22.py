"""
The module is designed by team Robokit of Phystech Lyceum and team Starkit
of MIPT under mentorship of Azer Babaev.
The module is designed for strategy of soccer game by forward and goalkeeper.
"""
"""
Possible values of 1-st argument role: 
forward, goalkeeper, penalty_Shooter, penalty_Goalkeeper, run_test
Possible values of 2-nd argument: 1-8
Possible values of 3-rd argument: [0.0, 0.0, 0.0]
"""

PlayerID = '22'

import sys
import os
import math
import json
import time
import wx
import threading

current_work_directory = os.getcwd().replace('\\', '/') + '/'

SIMULATION = 4                       # 0 - Simulation without physics, 
                                     # 1 - Simulation synchronous with physics, 
                                     # 3 - Simulation streaming with physics
                                     # 4 - Simulation in Webots

#EXTERN = True

from Soccer.Localisation.class_Glob import Glob
from Soccer.Localisation.class_Local import *
from Soccer.strategy import Player
from Soccer.Motion.class_Motion_Webots_PB import Motion_sim
from launcher import *
from communication_manager import CommunicationManager
#controller_path = os.environ.get('WEBOTS_HOME').replace('\\', '/') + '/lib/controller/python39'
#sys.path.append(controller_path)
#from controller import *

global player_data
global robot
with open(current_work_directory + "teams.json", "r") as f:
    player_data = json.loads(f.read())

class Falling:
    def __init__(self):
        self.Flag = 0

class Pause:
    def __init__(self):
        self.Flag = False
global pause
pause = Pause()

def main_procedure():
    global player_data
    global pause
    robot_color = player_data[PlayerID]['robot_color']
    robot_number = player_data[PlayerID]['robot_number']
    team = player_data[PlayerID]['team']
    second_pressed_button = player_data[PlayerID]['second_pressed_button']
    initial_coord = player_data[PlayerID]['initial_coord']
    role = player_data[PlayerID]['role']
    robot = CommunicationManager(5, player_data[PlayerID]['IP_address'], player_data[PlayerID]['port'])
    sensors = {"gps_body": 5, "imu_head": 5, "imu_body": 5,  "camera": 20}
    robot.enable_sensors(sensors)
    th0 = threading.Thread(target=robot.run)
    th0.start()
    th0.join
    falling = Falling()
    if second_pressed_button == 0:
        player_super_cycle(falling, team, robot_number, SIMULATION, current_work_directory, robot, pause)
    print('teamColor = ', robot_color)
    print('Player is going to play without Game Controller')
    glob = Glob(SIMULATION, current_work_directory)
    glob.pf_coord = initial_coord
    #print(robot)
    motion = Motion_sim(glob, robot, None, pause)
    motion.sim_Start()
    motion.direction_To_Attack = -initial_coord[2]
    motion.activation()
    local = Local(motion, glob, coord_odometry = initial_coord)
    motion.local = local
    local.coordinate_record(odometry = True)
    motion.falling_Flag = 0
    player = Player(role, second_pressed_button, glob, motion, local)
    player.play_game()


class RedirectText(object):
    def __init__(self,aWxTextCtrl):
        self.out = aWxTextCtrl

    def write(self,string):
        self.out.WriteText(string)




class Main_Panel(wx.Frame):
    def __init__(self, *args, **kwargs):
        super(Main_Panel, self).__init__(*args, **kwargs)
        #self.main_procedure()
        self.InitUI()
        wx.CallLater(1000, self.main_procedure)

    def main_procedure(self):
        t1 = threading.Thread( target = main_procedure, args=())
        t1.setDaemon(True)
        t1.start()

    def InitUI(self):
        self.console_Panel = wx.Panel(self)
        panel = wx.Panel(self.console_Panel)
        self.log = wx.TextCtrl(self.console_Panel, -1, style=wx.TE_MULTILINE) #|wx.TE_READONLY) #|wx.HSCROLL)
        log_box = wx.BoxSizer(wx.VERTICAL)
        log_box.Add(panel,0,wx.TOP)
        log_box.Add(self.log, proportion = 1, flag=wx.EXPAND|wx.BOTTOM|wx.TOP)
        self.console_Panel.SetSizer(log_box)
        redir = RedirectText(self.log)
        sys.stdout = redir

        hbox = wx.BoxSizer()
        sizer = wx.GridSizer(1, 2, 2, 2)

        btn1 = wx.Button(panel, label='Exit')
        btn2 = wx.Button(panel, label='Pause')

        sizer.AddMany([btn1, btn2])

        hbox.Add(sizer, 0, wx.TOP)
        panel.SetSizer(hbox)


        btn1.Bind(wx.EVT_BUTTON, self.ShowMessage1)
        btn2.Bind(wx.EVT_BUTTON, self.ShowMessage2)

        self.SetSize((300, 200))
        global player_data
        robot_color = player_data[PlayerID]['robot_color']
        robot_number = player_data[PlayerID]['robot_number']
        team = robot_color
        #player_number = robot_number
        title = 'Team ' + str(team) + ' player '+ str(robot_number)
        self.SetTitle(title)
        width, height = wx.GetDisplaySize().Get()
        teamColor = robot_color
        if teamColor == 'red':
            x_position = width - 300 * (5- robot_number)
        else:
            x_position = width - 300 * (3- robot_number)
        self.SetPosition((x_position, height -225))
        #self.Centre()

    def ShowMessage1(self, event):
        print('Exit button pressed')
        sys.stdout = sys.__stdout__
        sys.exit(0)

    def ShowMessage2(self, event):
        global pause
        if pause.Flag:
            pause.Flag = False
        else:
            pause.Flag = True
        print('Pause button pressed')


def main():
    app = wx.App()
    ex = Main_Panel(None)
    ex.Show()
    app.MainLoop()

main()



