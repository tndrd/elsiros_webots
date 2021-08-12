# Heavilly based on https://github.com/RoboCup-Humanoid-TC/webots/blob/release/projects/samples/contests/robocup/controllers/referee/referee.py by Ludovic Hoefer from Rhoban team


import copy
import socket
import sys
import os
import subprocess
import json
import math
import time
import traceback
from types import SimpleNamespace
from controller import Supervisor, AnsiCodes, Node
from field import Field
from gamestate import GameState

DISABLE_ACTUATORS_MIN_DURATION = 1.0      # The minimal simulated time [s] until enabling actuators again after a reset
STATUS_PRINT_PERIOD = 20                  # Real time between two status updates in seconds
IN_PLAY_TIMEOUT = 10                      # time after which the ball is considered in play even if it was not kicked
RED_COLOR = 0xd62929                      # red team color used for the display
BLUE_COLOR = 0x2943d6                     # blue team color used for the display
WHITE_COLOR = 0xffffff                    # white color used for the display
BLACK_COLOR = 0x000000                    # black color used for the display
GOAL_WIDTH = 1.0   
GOAL_HALF_WIDTH = GOAL_WIDTH / 2
RESTART_MARKER_WIDTH = 0.65

# game interruptions requiring a free kick procedure
GAME_INTERRUPTIONS = {
    'DIRECT_FREEKICK': 'direct free kick',
    'INDIRECT_FREEKICK': 'indirect free kick',
    'PENALTYKICK': 'penalty kick',
    'CORNERKICK': 'corner kick',
    'GOALKICK': 'goal kick',
    'THROWIN': 'throw in'}

global game

# start the webots supervisor
supervisor = Supervisor()
time_step = int(supervisor.getBasicTimeStep())
time_count = 0

log_file = open('log.txt', 'w')

def distance2(v1, v2):
    return math.sqrt((v1[0] - v2[0]) ** 2 + (v1[1] - v2[1]) ** 2)

def log(message, msg_type, force_flush=True):
    try:
        if type(message) is list:
            for m in message:
                log(m, msg_type, False)
            if log_file and force_flush:
                log_file.flush()
            return
        if msg_type == 'Warning':
            console_message = f'{AnsiCodes.YELLOW_FOREGROUND}{AnsiCodes.BOLD}{message}{AnsiCodes.RESET}'
        elif msg_type == 'Error':
            console_message = f'{AnsiCodes.RED_FOREGROUND}{AnsiCodes.BOLD}{message}{AnsiCodes.RESET}'
        else:
            console_message = message
        print(console_message, file=sys.stderr if msg_type == 'Error' else sys.stdout, flush=True)
        if log_file:
            #real_time = int(1000 * (time.time() - log.real_time)) / 1000
            #log_file.write(f'[{real_time:08.3f}|{time_count / 1000:08.3f}] {msg_type}: {message}\n')  # log real and virtual times
            log_file.write(f'{msg_type}: {message}\n')  # log real and virtual times
            if force_flush:
                log_file.flush()    
    except Exception:
        pass
    

def info(message):
    log(message, 'Info')

def warning(message):
    log(message, 'Warning')

def error(message, fatal=False):
    log(message, 'Error')


def read_team(json_path):
    team = None
    try:
        with open(json_path) as json_file:
            team = json.load(json_file)
            for field_name in ["name", "players"]:
                if field_name not in team:
                    raise RuntimeError(f"Missing field {field_name}")
            if len(team['players']) == 0:
                warning(f"No players found for team {team['name']}")
            count = 1
            for p_key, p in team['players'].items():
                if int(p_key) != count:
                    raise RuntimeError(f'Wrong team player number: expecting "{count}", found "{p_key}".')
                for field_name in ['proto', 'borderStartingPose', 'readyStartingPose', 'reentryStartingPose', 'shootoutStartingPose', 'goalKeeperStartingPose']:
                    if field_name not in p:
                        raise RuntimeError(f"Missing field {field_name} in player {p_key}")
                count += 1
    except Exception:
        error(f"Failed to read file {json_path} with the following error:\n{traceback.format_exc()}", fatal=True)
    return team        

# determine configuration file name
game_config_file = os.environ['WEBOTS_ROBOCUP_GAME'] if 'WEBOTS_ROBOCUP_GAME' in os.environ \
    else os.path.join(os.getcwd(), 'game.json')
if not os.path.isfile(game_config_file):
    error(f'Cannot read {game_config_file} game config file.', fatal=True)

# read configuration files
with open(game_config_file) as json_file:
    game = json.loads(json_file.read(), object_hook=lambda d: SimpleNamespace(**d))
red_team = read_team(game.red.config)
blue_team = read_team(game.blue.config)
# if the game.json file is malformed with ids defined as string instead of int, we need to convert them to int:
if not isinstance(game.red.id, int):
    game.red.id = int(game.red.id)
if not isinstance(game.blue.id, int):
    game.blue.id = int(game.blue.id)

# finalize the game object
if not hasattr(game, 'minimum_real_time_factor'):
    game.minimum_real_time_factor = 3  # we garantee that each time step lasts at least 3x simulated time
if game.minimum_real_time_factor == 0:  # speed up non-real time tests
    REAL_TIME_BEFORE_FIRST_READY_STATE = 5
    HALF_TIME_BREAK_REAL_TIME_DURATION = 2
if not hasattr(game, 'press_a_key_to_terminate'):
    game.press_a_key_to_terminate = False
if game.type not in ['NORMAL', 'KNOCKOUT', 'PENALTY']:
    error(f'Unsupported game type: {game.type}.', fatal=True)
game.penalty_shootout = game.type == 'PENALTY'
info(f'Minimum real time factor is set to {game.minimum_real_time_factor}.')
if game.minimum_real_time_factor == 0:
    info('Simulation will run as fast as possible, real time waiting times will be minimal.')
else:
    info(f'Simulation will guarantee a maximum {1 / game.minimum_real_time_factor:.2f}x speed for each time step.')
field_size = getattr(game, 'class').lower()
game.field = Field(field_size)    

def flip_pose(pose):
    pose['translation'][0] = -pose['translation'][0]
    pose['rotation'][3] = math.pi - pose['rotation'][3]


def flip_poses(player):
    flip_pose(player['borderStartingPose'])
    flip_pose(player['readyStartingPose'])    
    flip_pose(player['reentryStartingPose'])
    flip_pose(player['shootoutStartingPose'])
    flip_pose(player['goalKeeperStartingPose'])    

def flip_sides():  # flip sides (no need to notify GameController, it does it automatically)
    game.side_left = game.red.id if game.side_left == game.blue.id else game.blue.id
    for team in [red_team, blue_team]:
        for number in team['players']:
            flip_poses(team['players'][number])
    update_team_display()

def get_penalty_shootout_msg():
    trial = game.penalty_shootout_count + 1
    name = "penalty shoot-out"
    if game.penalty_shootout_count >= 10:
        name = f"extended {name}"
        trial -= 10
    return f"{name} {trial}/10"

def perform_status_update():
    now = time.time()
    if not hasattr(game, "last_real_time"):
        game.last_real_time = now
        game.last_time_count = time_count
    elif now - game.last_real_time > STATUS_PRINT_PERIOD:
        elapsed_real = now - game.last_real_time
        elapsed_simulation = (time_count - game.last_time_count) / 1000
        speed_factor = elapsed_simulation / elapsed_real
        messages = [f"Avg speed factor: {speed_factor:.3f} (over last {elapsed_real:.2f} seconds)"]
        if game.state is None:
            messages.append("No messages received from GameController yet")
        else:
            messages.append(f"state: {game.state.game_state}, remaining time: {game.state.seconds_remaining}")
            if game.state.secondary_state in GAME_INTERRUPTIONS:
                messages.append(f"  sec_state: {game.state.secondary_state} phase: {game.state.secondary_state_info[1]}")
        if game.penalty_shootout:
            messages.append(f"{get_penalty_shootout_msg()}")
        messages = [f"STATUS: {m}" for m in messages]
        info(messages)
        game.last_real_time = now
        game.last_time_count = time_count    



def init_team(team):
    # check validity of team files
    # the players IDs should be "1", "2", "3", "4" for four players, "1", "2", "3" for three players, etc.
    count = 1
    for number in team['players']:
        count += 1
        player = team['players'][number]
        player['outside_circle'] = True
        player['outside_field'] = True
        player['inside_field'] = False
        player['on_outer_line'] = False
        player['inside_own_side'] = False
        player['outside_goal_area'] = True
        player['outside_penalty_area'] = True
        player['left_turf_time'] = None
        # Stores tuples of with (time_count[int], dic) at a 1Hz frequency
        player['history'] = []
        window_size = int(1000 / time_step)  # one second window size
        player['velocity_buffer'] = [[0] * 6] * window_size
        player['ball_handling_start'] = None
        player['ball_handling_last'] = None

def spawn_team(team, red_on_right, children):
    color = team['color']
    nb_players = len(team['players'])
    team_id = game.red.id if color == 'red' else game.blue.id
    for number in team['players']:
        player = team['players'][number]
        model = player['proto']
        n = int(number) - 1
        port = game.red.ports[n] if color == 'red' else game.blue.ports[n]
        if red_on_right:  # symmetry with respect to the central line of the field
            flip_poses(player)
        defname = color.upper() + '_PLAYER_' + number
        halfTimeStartingTranslation = player['borderStartingPose']['translation']
        halfTimeStartingRotation = player['borderStartingPose']['rotation']
        '''
        string = f'DEF {defname} {model}{{name "{color} player {number}" translation ' + \
            f'{halfTimeStartingTranslation[0]} {halfTimeStartingTranslation[1]} {halfTimeStartingTranslation[2]} rotation ' + \
            f'{halfTimeStartingRotation[0]} {halfTimeStartingRotation[1]} {halfTimeStartingRotation[2]} ' + \
            f'{halfTimeStartingRotation[3]} controllerArgs ["{port}" "{nb_players}"'
        hosts = game.red.hosts if color == 'red' else game.blue.hosts
        for h in hosts:
            string += f', "{h}"'
        string += '] }}'
        '''
        # Controller args by referee: 0 0 0 team_id robot_number
        string = f'DEF {defname} {model}{{name "{color} player {number}" translation ' + \
            f'{halfTimeStartingTranslation[0]} {halfTimeStartingTranslation[1]} {halfTimeStartingTranslation[2]} rotation ' + \
            f'{halfTimeStartingRotation[0]} {halfTimeStartingRotation[1]} {halfTimeStartingRotation[2]} ' + \
            f'{halfTimeStartingRotation[3]} controllerArgs [ "0" "0" "0" "{team_id}" "{number}" ] teamColor "{color}" playerNumber "{number}" }}'

        children.importMFNodeFromString(-1, string)
        player['robot'] = supervisor.getFromDef(defname)
        #player['position'] = player['robot'].getCenterOfMass()
        info(f'Spawned {defname} {model} on port {port} at borderStartingPose: translation (' +
             f'{halfTimeStartingTranslation[0]} {halfTimeStartingTranslation[1]} {halfTimeStartingTranslation[2]}), ' +
             f'rotation ({halfTimeStartingRotation[0]} {halfTimeStartingRotation[1]} {halfTimeStartingRotation[2]} ' +
             f'{halfTimeStartingRotation[3]}).')     

def reset_player(color, number, pose, custom_t=None, custom_r=None):
    team = red_team if color == 'red' else blue_team
    player = team['players'][number]
    robot = player['robot']
    if robot is None:
        return
    robot.loadState('__init__')
    #list_player_solids(player, color, number)
    translation = robot.getField('translation')
    rotation = robot.getField('rotation')
    t = custom_t if custom_t else player[pose]['translation']
    r = custom_r if custom_r else player[pose]['rotation']
    translation.setSFVec3f(t)
    rotation.setSFRotation(r)
    robot.resetPhysics()
    player['stabilize'] = 5  # stabilize after 5 simulation steps
    player['stabilize_translation'] = t
    player['stabilize_rotation'] = r
    player['position'] = t
    info(f'{color.capitalize()} player {number} reset to {pose}: ' +
         f'translation ({t[0]} {t[1]} {t[2]}), rotation ({r[0]} {r[1]} {r[2]} {r[3]}).')
    info(f'Disabling actuators of {color} player {number}.')
    robot.getField('customData').setSFString('penalized')
    player['enable_actuators_at'] = time_count + int(DISABLE_ACTUATORS_MIN_DURATION * 1000)


def reset_teams(pose):
    for number in red_team['players']:
        reset_player('red', str(number), pose)
    for number in blue_team['players']:
        reset_player('blue', str(number), pose)  

'''
def human_referee_receive():
    data = None
    while True:
        try:
            data, peer = game.human_referee_socket.recvfrom(1) # receive one byte
            game.human_referee_buffer.append(data)
            if game.human_referee_buffer[-1] == b'\n':
                game.human_referee_message = b''.join(game.human_referee_buffer[:-1])
                game.human_referee_buffer.clear()
        except BlockingIOError:
            return
        except Exception as e:
            error(f'UDP input failure: {e}')
            game.human_referee_disconnected = True
            return
        if not data:
            error('No UDP data received')
            game.human_referee_disconnected = True
            return

def human_referee_parse(message):
    if message.find(b'place ball') != -1:
        print('Placing ball')
        game.ball_translation.setSFVec3f([0,0,0])
    if message.find(b'set penalty') != -1:
        print('Placing ball and robots for penalty')
        game.ball_translation.setSFVec3f([-3.0, 0, 0])
        reset_player('red', '1', 'shootoutStartingPose')
        reset_player('blue', '1', 'goalKeeperStartingPose')


def human_referee_send(message):
    game.human_referee_socket.sendall(message.encode('ascii'))  
'''

def format_time(s):
    seconds = str(s % 60)
    minutes = str(int(s / 60))
    if len(minutes) == 1:
        minutes = '0' + minutes
    if len(seconds) == 1:
        seconds = '0' + seconds
    return minutes + ':' + seconds


def update_time_display():
    if game.state:
        s = game.state.seconds_remaining
        if s < 0:
            s = -s
            sign = '-'
        else:
            sign = ' '
        value = format_time(s)
    else:
        sign = ' '
        value = '--:--'
    supervisor.setLabel(6, sign + value, 0, 0, game.font_size, 0x000000, 0.2, game.font)


def update_state_display():
    if game.state:
        #print(f'update_state_display at {game.state.seconds_remaining} ')        
        state = game.state.game_state[6:]
        if state == 'READY' or state == 'SET':  # kickoff
            color = RED_COLOR if game.kickoff == game.red.id else BLUE_COLOR
        else:
            color = 0x000000
    else:
        state = ''
        color = 0x000000
    supervisor.setLabel(7, ' ' * 41 + state, 0, 0, game.font_size, color, 0.2, game.font)
    update_details_display()


def update_score_display():
    if game.state:
        red = 0 if game.state.teams[0].team_color == 'RED' else 1
        blue = 1 if red == 0 else 0
        red_score = str(game.state.teams[red].score)
        blue_score = str(game.state.teams[blue].score)
    else:
        red_score = '0'
        blue_score = '0'
    if game.side_left == game.blue.id:
        offset = 21 if len(blue_score) == 2 else 22
        score = ' ' * offset + blue_score + '-' + red_score
    else:
        offset = 21 if len(red_score) == 2 else 22
        score = ' ' * offset + red_score + '-' + blue_score
    supervisor.setLabel(5, score, 0, 0, game.font_size, BLACK_COLOR, 0.2, game.font)


def update_team_details_display(team, side, strings):
    for n in range(len(team['players'])):
        robot_info = game.state.teams[side].players[n]
        strings.background += '█  '
        if robot_info.number_of_warnings > 0:  # a robot can have both a warning and a yellow card
            strings.warning += '■  '
            strings.yellow_card += ' ■ ' if robot_info.number_of_yellow_cards > 0 else '   '
        else:
            strings.warning += '   '
            strings.yellow_card += '■  ' if robot_info.number_of_yellow_cards > 0 else '   '
        strings.red_card += '■  ' if robot_info.number_of_red_cards > 0 else '   '
        strings.white += str(n + 1) + '██'
        strings.foreground += f'{robot_info.secs_till_unpenalized:02d} ' if robot_info.secs_till_unpenalized != 0 else '   '


def update_details_display():
    if not game.state:
        return
    red = 0 if game.state.teams[0].team_color == 'RED' else 1
    blue = 1 if red == 0 else 0
    if game.side_left == game.red.id:
        left = red
        right = blue
        left_team = red_team
        right_team = blue_team
        left_color = RED_COLOR
        right_color = BLUE_COLOR
    else:
        left = blue
        right = red
        left_team = blue_team
        right_team = red_team
        left_color = BLUE_COLOR
        right_color = RED_COLOR

    class StringObject:
        pass

    strings = StringObject()
    strings.foreground = ' ' + format_time(game.state.secondary_seconds_remaining) + '  ' \
                         if game.state.secondary_seconds_remaining > 0 else ' ' * 8
    strings.background = ' ' * 7
    strings.warning = strings.background
    strings.yellow_card = strings.background
    strings.red_card = strings.background
    strings.white = '█' * 7
    update_team_details_display(left_team, left, strings)
    strings.left_background = strings.background
    strings.background = ' ' * 28
    space = 21 - len(left_team['players']) * 3
    strings.white += '█' * space
    strings.warning += ' ' * space
    strings.yellow_card += ' ' * space
    strings.red_card += ' ' * space
    strings.foreground += ' ' * space
    update_team_details_display(right_team, right, strings)
    strings.right_background = strings.background
    del strings.background
    space = 12 - 3 * len(right_team['players'])
    strings.white += '█' * (22 + space)
    secondary_state = ' ' * 41 + game.state.secondary_state[6:]
    sr = IN_PLAY_TIMEOUT - game.interruption_seconds + game.state.seconds_remaining \
        if game.interruption_seconds is not None else 0
    if sr > 0:
        secondary_state += ' ' + format_time(sr)
    if game.state.secondary_state[6:] != 'NORMAL' or game.state.secondary_state_info[1] != 0:
        secondary_state += ' [' + str(game.state.secondary_state_info[1]) + ']'
    if game.interruption_team is not None:  # interruption
        secondary_state_color = RED_COLOR if game.interruption_team == game.red.id else BLUE_COLOR
    else:
        secondary_state_color = BLACK_COLOR
    y = 0.0465  # vertical position of the second line
    supervisor.setLabel(10, strings.left_background, 0, y, game.font_size, left_color, 0.2, game.font)
    supervisor.setLabel(11, strings.right_background, 0, y, game.font_size, right_color, 0.2, game.font)
    supervisor.setLabel(12, strings.white, 0, y, game.font_size, WHITE_COLOR, 0.2, game.font)
    supervisor.setLabel(13, strings.warning, 0, 2 * y, game.font_size, 0x0000ff, 0.2, game.font)
    supervisor.setLabel(14, strings.yellow_card, 0, 2 * y, game.font_size, 0xffff00, 0.2, game.font)
    supervisor.setLabel(15, strings.red_card, 0, 2 * y, game.font_size, 0xff0000, 0.2, game.font)
    supervisor.setLabel(16, strings.foreground, 0, y, game.font_size, BLACK_COLOR, 0.2, game.font)
    supervisor.setLabel(17, secondary_state, 0, y, game.font_size, secondary_state_color, 0.2, game.font)


def update_team_display():
    # red and blue backgrounds
    left_color = RED_COLOR if game.side_left == game.red.id else BLUE_COLOR
    right_color = BLUE_COLOR if game.side_left == game.red.id else RED_COLOR
    supervisor.setLabel(2, ' ' * 7 + '█' * 14, 0, 0, game.font_size, left_color, 0.2, game.font)
    supervisor.setLabel(3, ' ' * 26 + '█' * 14, 0, 0, game.font_size, right_color, 0.2, game.font)
    # white background and names
    left_team = red_team if game.side_left == game.red.id else blue_team
    right_team = red_team if game.side_left == game.blue.id else blue_team
    team_names = 7 * '█' + (13 - len(left_team['name'])) * ' ' + left_team['name'] + \
        ' █████ ' + right_team['name'] + ' ' * (13 - len(right_team['name'])) + '█' * 22
    supervisor.setLabel(4, team_names, 0, 0, game.font_size, WHITE_COLOR, 0.2, game.font)
    update_score_display()


def setup_display():
    update_team_display()
    update_time_display()
    update_state_display()

def team_index(color):
    if color not in ['red', 'blue']:
        raise RuntimeError(f'Wrong color passed to team_index(): \'{color}\'.')
    id = game.red.id if color == 'red' else game.blue.id
    index = 0 if game.state.teams[0].team_number == id else 1
    if game.state.teams[index].team_number != id:
        raise RuntimeError(f'Wrong team number set in team_index(): {id} != {game.state.teams[index].team_number}')
    return index    

def update_team_penalized_from_gamecontroller(team):
    color = team['color']
    index = team_index(color)
    for number in team['players']:
        player = team['players'][number]
        if player['robot'] is None:
            continue
        p = game.state.teams[index].players[int(number) - 1]    
        if p.penalty != 0:
            if 'penalized' not in player:
                info(f'Robot {color} {index+1} penalized by GC')
                player['penalized'] = 'penalized_by_gamecontroller'
                # sending robot far away from the field
                t = copy.deepcopy(player['reentryStartingPose']['translation'])
                t[0] = 50
                t[1] = (10 + int(number)) * (1 if color == 'red' else -1)
                #reset_player(color, number, 'reentryStartingPose', t)            
                reset_player(color, number, 'reentryStartingPose') # will set 'penalized' in CustomData and proper 'Secs until unpenalized'
        #else:
        #    if 'penalized' in player:
        #        del player['penalized']
        #        customData = player['robot'].getField('customData')
        #        info(f'Enabling actuators of {color} player {number}.')
        #        customData.setSFString('')                
        #        info(f'Robot {color} {index+1} is NOT penalized by GC anymore')
        if 'enable_actuators_at' in player:
            timing_ok = time_count >= player['enable_actuators_at']
            penalty_ok = 'penalized' not in player or p.penalty == 0
            if timing_ok and penalty_ok:
                customData = player['robot'].getField('customData')
                info(f'Enabling actuators of {color} player {number}.')
                customData.setSFString('')
                del player['enable_actuators_at']
                if 'penalized' in player:
                    del player['penalized']        




def update_penalized():
    update_team_penalized_from_gamecontroller(red_team)
    update_team_penalized_from_gamecontroller(blue_team)        

def game_controller_send(message):
    if message[:6] == 'STATE:' or message[:6] == 'SCORE:' or message == 'DROPPEDBALL':
        # we don't want to send twice the same STATE or SCORE message
        #if game_controller_send.sent_once == message:
        #    return False
        game_controller_send.sent_once = message
        if message[6:] in ['READY', 'SET']:
            game.wait_for_state = message[6:]
        elif message[6:] == 'PLAY':
            game.wait_for_state = 'PLAYING'
        elif (message[:6] == 'SCORE:' or
              message == 'DROPPEDBALL'):
            #game.wait_for_state = 'FINISHED' if game.penalty_shootout else 'READY'
            game.wait_for_state = 'FINISHED' if game.state.secondary_state == 'STATE_PENALTYSHOOT' else 'READY'
        elif message[6:] == "PENALTY-SHOOTOUT":
            game.wait_for_state = 'INITIAL'
        info(f'game.wait_for_state={game.wait_for_state}')
    if ':' in message:
        msg_start = message.split(':', 1)[0]
        if msg_start in GAME_INTERRUPTIONS:
            if 'ABORT' in message or 'EXECUTE' in message:
                game.wait_for_sec_state = 'NORMAL'
                game.wait_for_sec_phase = 0
            else:
                game.wait_for_sec_state = msg_start
                if 'READY' in message:
                    game.wait_for_sec_phase = 1
                elif 'PREPARE' in message:
                    game.wait_for_sec_phase = 2
                else:
                    game.wait_for_sec_phase = 0
            info(f"Waiting for secondary state: {game.wait_for_sec_state}:{game.wait_for_sec_phase}")
    game_controller_send.id += 1
    if message[:6] != 'CLOCK:':
        info(f'Sending {game_controller_send.id}:{message} to GameController.')
    message = f'{game_controller_send.id}:{message}\n'
    game.controller.sendall(message.encode('ascii'))
    #info(f'sending {message.strip()} to GameController')
    game_controller_send.unanswered[game_controller_send.id] = message.strip()
    answered = False
    sent_id = game_controller_send.id
    while True:
        try:
            answers = game.controller.recv(1024).decode('ascii').split('\n')
            for answer in answers:
                if answer == '':
                    continue
                try:
                    id, result = answer.split(':')
                    if int(id) == sent_id:
                        answered = True
                except ValueError:
                    error(f'Cannot split {answer}', fatal=True)
                try:
                    answered_message = game_controller_send.unanswered[int(id)]
                    del game_controller_send.unanswered[int(id)]
                except KeyError:
                    error(f'Received acknowledgment message for unknown message: {id}', fatal=True)
                    continue
                if result == 'OK':
                    #info(f'Received correct GameController answer to {answered_message}.') #will spam with clock
                    continue
                if result == 'INVALID':
                    error(f'Received invalid answer from GameController for message {answered_message}.', fatal=True)
                elif result == 'ILLEGAL':
                    info_msg = f"Received illegal answer from GameController for message {answered_message}."
                    if "YELLOW" in message:
                        warning(info_msg)
                    else:
                        error(info_msg, fatal=True)
                else:
                    error(f'Received unknown answer from GameController: {answer}.', fatal=True)
        except BlockingIOError:
            if answered or ':CLOCK:' in message:
                break
            else:  # keep sending CLOCK messages to keep the GameController happy
                info(f'Waiting for GameController to answer to {message.strip()}.')
                time.sleep(0.2)
                game_controller_send.id += 1
                clock_message = f'{game_controller_send.id}:CLOCK:{time_count}\n'
                game.controller.sendall(clock_message.encode('ascii'))
                game_controller_send.unanswered[game_controller_send.id] = clock_message.strip()
    # We are waiting for a specific update from the GC before testing anything else
    while game.wait_for_state is not None or game.wait_for_sec_state is not None or game.wait_for_sec_phase is not None:
        game_controller_receive()

    return True     

def game_controller_receive():
    #All game state times are hardcoded in c:\Egor\Starkitrobots\Robokit\GameController\src\data\hl\HLSim.java 
    data = None
    ip = None
    while True:
        #if game_controller_udp_filter and ip and ip not in game_controller_receive.others:
        #    game_controller_receive.others.append(ip)
        #    warning(f'Ignoring UDP packets from {ip} not matching GAME_CONTROLLER_UDP_FILTER={game_controller_udp_filter}.')
        try:
            data, peer = game.udp.recvfrom(GameState.sizeof())
            #ip, port = peer
            #if game_controller_udp_filter is None or game_controller_udp_filter == ip:
            #    break
            #else:
            #    continue
            break
        except BlockingIOError:
            return
        except Exception as e:
            error(f'UDP input failure: {e}')
            return
        if not data:
            error('No UDP data received')
            return
    previous_seconds_remaining = game.state.seconds_remaining if game.state else 0
    previous_secondary_seconds_remaining = game.state.secondary_seconds_remaining if game.state else 0
    previous_state = game.state.game_state if game.state else None
    previous_sec_state = game.state.secondary_state if game.state else None
    previous_sec_phase = game.state.secondary_state_info[1] if game.state else None
    if game.state:
        red = 0 if game.state.teams[0].team_color == 'RED' else 1
        blue = 1 if red == 0 else 0
        previous_red_score = game.state.teams[red].score
        previous_blue_score = game.state.teams[blue].score
    else:
        previous_red_score = 0
        previous_blue_score = 0

    game.state = GameState.parse(data)
    #info(game.state.teams)

    if previous_state != game.state.game_state:
        info(f'New state received from GameController: {game.state.game_state}.')
        if game.wait_for_state is not None:
            if 'STATE_' + game.wait_for_state != game.state.game_state:
                warning(f'Received unexpected state from GameController: {game.state.game_state} ' +
                        f'while expecting {game.wait_for_state}')
            else:
                info(f"State has succesfully changed to {game.wait_for_state}")
                game.wait_for_state = None
    new_sec_state = game.state.secondary_state
    new_sec_phase = game.state.secondary_state_info[1]
    if previous_sec_state != new_sec_state or previous_sec_phase != new_sec_phase:
        info(f'New secondary state received from GameController: {new_sec_state}, phase {new_sec_phase}.')
        if game.wait_for_sec_state is not None or game.wait_for_sec_phase is not None:
            if 'STATE_' + game.wait_for_sec_state != new_sec_state or new_sec_phase != game.wait_for_sec_phase:
                warning(f'Received unexpected secondary state from GameController: {new_sec_state}:{new_sec_phase} ' +
                        f'while expecting {game.wait_for_sec_state}:{game.wait_for_sec_phase}')
            else:
                info(f"Secondary state has succesfully changed to {new_sec_state}:{new_sec_phase}")
                game.wait_for_sec_state = None
                game.wait_for_sec_phase = None

    #if game.state.game_state == 'STATE_PLAYING' and \
    #   game.state.secondary_seconds_remaining == 0 and previous_secondary_seconds_remaining > 0:
    #    if game.in_play is None and game.phase == 'KICKOFF':
    #        info('Ball in play, can be touched by any player (10 seconds elapsed after kickoff).')
    #        game.in_play = time_count
    #        game.ball_last_move = time_count
    if previous_seconds_remaining != game.state.seconds_remaining:
        #allow_in_play = game.wait_for_sec_state is None and game.wait_for_sec_phase is None
        #if allow_in_play and game.state.secondary_state == "STATE_NORMAL" and game.interruption_seconds is not None:
        #    if game.interruption_seconds - game.state.seconds_remaining > IN_PLAY_TIMEOUT:
        #        if game.in_play is None:
        #            info('Ball in play, can be touched by any player (10 seconds elapsed).')
        #            game.in_play = time_count
        #            game.ball_last_move = time_count
        #            game.interruption = None
        #            game.interruption_step = None
        #            game.interruption_step_time = 0
        #            game.interruption_team = None
        #            game.interruption_seconds = None
        update_time_display()
    red = 0 if game.state.teams[0].team_color == 'RED' else 1
    blue = 1 if red == 0 else 0
    if previous_red_score != game.state.teams[red].score or previous_blue_score != game.state.teams[blue].score:
        update_score_display()

    #print(game.state.game_state)
    secondary_state = game.state.secondary_state
    secondary_state_info = game.state.secondary_state_info
    '''
    if secondary_state[0:6] == 'STATE_' and secondary_state[6:] in GAME_INTERRUPTIONS:
        kick = secondary_state[6:]
        step = secondary_state_info[1]
        delay = (time_count - game.interruption_step_time) / 1000
        if step == 0 and game.interruption_step != step:
            game.interruption_step = step
            game.interruption_step_time = time_count
            info(f'Awarding a {GAME_INTERRUPTIONS[kick]}.')
        elif (step == 1 and game.interruption_step != step and game.state.secondary_seconds_remaining <= 0 and
              delay >= SIMULATED_TIME_INTERRUPTION_PHASE_1):
            game.interruption_step = step
            game_controller_send(f'{kick}:{secondary_state_info[0]}:PREPARE')
            game.interruption_step_time = time_count
            info(f'Prepare for {GAME_INTERRUPTIONS[kick]}.')
        elif step == 2 and game.interruption_step != step and game.state.secondary_seconds_remaining <= 0:
            game.interruption_step = step
            opponent_team = blue_team if secondary_state_info[0] == game.red.id else red_team
            check_team_away_from_ball(opponent_team, game.field.opponent_distance_to_ball)
            game_controller_send(f'{kick}:{secondary_state_info[0]}:EXECUTE')
            info(f'Execute {GAME_INTERRUPTIONS[kick]}.')
            game.interruption_seconds = game.state.seconds_remaining
            if game.interruption_seconds == 0:
                game.interruption_seconds = None
    elif secondary_state not in ['STATE_NORMAL', 'STATE_OVERTIME', 'STATE_PENALTYSHOOT']:
        print(f'GameController {game.state.game_state}:{secondary_state}: {secondary_state_info}')
    '''
    update_penalized()
    if previous_state != game.state.game_state or \
       previous_sec_state != new_sec_state or previous_sec_phase != new_sec_phase: #or \
       #previous_secondary_seconds_remaining != game.state.secondary_seconds_remaining or \
       #game.state.seconds_remaining <= 0:
        update_state_display()     


def place_ball(target_location, enforce_distance=True):
    '''
    if enforce_distance:
        target_location[2] = 0  # Set position along z-axis to 0 for all 'game.field.point_inside' checks
        step = 1
        info(f"GI placing ball to {target_location}")
        while step <= 4 and is_robot_near(target_location, game.field.place_ball_safety_dist):
            if step == 1:
                info('Reset of penalized robots')
                reset_pos_penalized_robots_near(target_location, game.field.place_ball_safety_dist)
            elif step == 2:
                info('Penalizing fallen robots')
                penalize_fallen_robots_near(target_location, game.field.place_ball_safety_dist)
            elif step == 3:
                info('Finding alternative locations')
                for loc in get_alternative_ball_locations(target_location):
                    info(f"Testing alternative location: {loc}")
                    # TODO: ?should it only allow point outside penalty area?
                    if game.field.point_inside(loc) and not is_robot_near(loc, game.field.place_ball_safety_dist):
                        info(f"Set alternative location to: {loc}")
                        target_location = loc.tolist()
                        break
            elif step == 4:
                info(f"Pushing robots away from {target_location}")
                move_robots_away(target_location)
            step += 1
    '''
    target_location[2] = game.ball_radius
    game.ball.resetPhysics()
    game.ball_translation.setSFVec3f(target_location)
    game.ball_set_kick = False
    reset_ball_touched()
    info(f'Ball respawned at {target_location[0]} {target_location[1]} {target_location[2]}.')

def move_ball_away():
    """Places ball far away from field for phases where the referee is supposed to hold it in it's hand"""
    target_location = [100, 100, game.ball_radius + 0.05]
    game.ball.resetPhysics()
    game.ball_translation.setSFVec3f(target_location)
    info("Moved ball out of the field temporarily")

def set_ball_touched(team_color, player_number):
    game.ball_previous_touch_team = game.ball_last_touch_team
    game.ball_previous_touch_player_number = game.ball_last_touch_player_number
    game.ball_last_touch_team = team_color
    game.ball_last_touch_player_number = player_number
    game.dropped_ball = False

def reset_ball_touched():
    game.ball_previous_touch_team = None
    game.ball_previous_touch_player_number = None
    game.ball_last_touch_team = None
    game.ball_last_touch_player_number = None    

def kickoff():
    game.kickoff = game.blue.id #TODO: DIRTY HARDCODE!!!!
    color = 'red' if game.kickoff == game.red.id else 'blue'
    info(f'Kick-off is {color}.')
    game.phase = 'KICKOFF'
    game.ball_kick_translation[0] = 0
    game.ball_kick_translation[1] = 0
    game.ball_set_kick = True
    game.ball_first_touch_time = 0
    game.in_play = None
    game.ball_must_kick_team = color
    #reset_ball_touched()
    game.ball_left_circle = None  # one can score only after ball went out of the circle
    game.can_score = False        # or was touched by another player
    game.can_score_own = False
    game.kicking_player_number = None
    move_ball_away()
    info(f'Ball not in play, will be kicked by a player from the {game.ball_must_kick_team} team.')

def player_has_red_card(player):
    return 'penalized' in player and player['penalized'] == 'red_card'   

def is_goalkeeper(team, id):
    n = game.state.teams[0].team_number
    index = 0 if (n == game.red.id and team == red_team) or (n == game.blue.id and team == blue_team) else 1
    return game.state.teams[index].players[int(id) - 1].goalkeeper       

def is_penalty_kicker(team, id):
    return not is_goalkeeper(team, id)
    #for number in team['players']:
    #    if player_has_red_card(team['players'][number]):
    #        continue
    #    return id == number     

def set_penalty_positions():
    info(f"Setting positions for {get_penalty_shootout_msg()}")
    attacking_color = 'red' if (game.kickoff == game.red.id) else 'blue'
    if attacking_color == 'red':
        defending_color = 'blue'
        attacking_team = red_team
        defending_team = blue_team
    else:
        defending_color = 'red'
        attacking_team = blue_team
        defending_team = red_team
    for number in attacking_team['players']:
        #if player_has_red_card(attacking_team['players'][number]):
        #    continue
        if is_penalty_kicker(attacking_team, number):
            reset_player(attacking_color, number, 'shootoutStartingPose')
        else:
            reset_player(attacking_color, number, 'borderStartingPose')
            player = attacking_team['players'][number]
            del player['enable_actuators_at'] # Don't allow player on border to move
    for number in defending_team['players']:
        #if player_has_red_card(defending_team['players'][number]):
        #    continue
        if is_goalkeeper(defending_team, number):
            reset_player(defending_color, number, 'goalKeeperStartingPose')
        else:
            reset_player(defending_color, number, 'borderStartingPose')
            player = defending_team['players'][number]
            del player['enable_actuators_at'] # Don't allow player on border to move       

    x = game.field.penalty_mark_x if game.side_left == game.kickoff else -game.field.penalty_mark_x
    game.ball.resetPhysics()
    reset_ball_touched()
    game.in_play = None
    game.can_score = True
    game.can_score_own = False
    game.ball_set_kick = True
    game.ball_left_circle = True
    game.ball_must_kick_team = attacking_team['color']
    game.kicking_player_number = None
    game.ball_kick_translation[0] = x
    game.ball_kick_translation[1] = 0
    game.ball_translation.setSFVec3f(game.ball_kick_translation)

def update_team_contacts(team):
    #early_game_interruption = is_early_game_interruption()
    color = team['color']
    for number in team['players']:
        player = team['players'][number]
        robot = player['robot']
        if robot is None:
            continue
        '''
        l1 = len(player['velocity_buffer'])     # number of iterations
        l2 = len(player['velocity_buffer'][0])  # should be 6 (velocity vector size)
        player['velocity_buffer'][int(time_count / time_step) % l1] = robot.getVelocity()
        sum = [0] * l2
        for v in player['velocity_buffer']:
            for i in range(l2):
                sum[i] += v[i]
        player['velocity'] = [s / l1 for s in sum]
        '''
        n = robot.getNumberOfContactPoints(True)
        player['contact_points'] = []
        if n == 0:  # robot is asleep
            player['asleep'] = True
            continue
        player['asleep'] = False
        player['position'] = robot.getCenterOfMass()
        # if less then 3 contact points, the contacts do not include contacts with the ground, so don't update the following
        # value based on ground collisions
        if n >= 3:
            player['outside_circle'] = True        # true if fully outside the center cicle
            player['outside_field'] = True         # true if fully outside the field
            player['inside_field'] = True          # true if fully inside the field
            player['on_outer_line'] = False        # true if robot is partially on the line surrounding the field
            player['inside_own_side'] = True       # true if fully inside its own side (half field side)
            player['outside_goal_area'] = True     # true if fully outside of any goal area
            player['outside_penalty_area'] = True  # true if fully outside of any penalty area
            outside_turf = True                    # true if fully outside turf
            fallen = False
        else:
            outside_turf = False
            fallen = True
        for i in range(n):
            point = robot.getContactPoint(i)
            node = robot.getContactPointNode(i)
            if not node:
                continue
            name_field = node.getField('name')
            #member = 'unknown body part'
            if name_field:
                name = name_field.getSFString()
                #if name in player['tagged_solids']:
                #    member = player['tagged_solids'][name]
            if point[2] > game.field.turf_depth:  # not a contact with the ground
                #if not early_game_interruption and point in game.ball.contact_points:  # ball contact
                if point in game.ball.contact_points:  # ball contact
                    if game.ball_last_touch_team != color or game.ball_last_touch_player_number != int(number):
                        set_ball_touched(color, int(number))
                        info(f'Ball touched by {color} player {number}.')                                   

def update_ball_contacts():
    game.ball.contact_points = []
    for i in range(game.ball.getNumberOfContactPoints()):
        point = game.ball.getContactPoint(i)
        if point[2] <= game.field.turf_depth:  # contact with the ground
            continue
        game.ball.contact_points.append(point)
        break

red_team = read_team(game.red.config)
blue_team = read_team(game.blue.config)            

def update_contacts():
    """Only updates the contact of objects which are not asleep"""
    update_ball_contacts()
    update_team_contacts(red_team)
    update_team_contacts(blue_team)                   

def is_robot_near(position, min_dist):
    for team in [red_team, blue_team]:
        for number in team['players']:
            if distance2(position, team['players'][number]['position']) < min_dist:
                return True
    return False             

def throw_in(middle_line, left_side):
    possible_restart_points = []
    if middle_line == True:
        possible_restart_points.append( [0,  0,                    0] )
        possible_restart_points.append( [0, -RESTART_MARKER_WIDTH, 0] )
        possible_restart_points.append( [0,  RESTART_MARKER_WIDTH, 0] )
    else:
        if left_side == True:
            possible_restart_points.append( [-game.field.penalty_mark_x,  0,                    0] )
            possible_restart_points.append( [-game.field.penalty_mark_x, -RESTART_MARKER_WIDTH, 0] )
            possible_restart_points.append( [-game.field.penalty_mark_x,  RESTART_MARKER_WIDTH, 0] )    
        else:       
            possible_restart_points.append( [ game.field.penalty_mark_x,  0,                    0] )
            possible_restart_points.append( [ game.field.penalty_mark_x, -RESTART_MARKER_WIDTH, 0] )
            possible_restart_points.append( [ game.field.penalty_mark_x,  RESTART_MARKER_WIDTH, 0] )                

    for point in possible_restart_points:
        if not is_robot_near(point, game.field.place_ball_safety_dist):
            place_ball(point)
            break    


'''
def throw_in(left_side):
    # set the ball on the touch line for throw in
    sign = -1 if left_side else 1
    game.ball_kick_translation[0] = game.ball_exit_translation[0]
    game.ball_kick_translation[1] = sign * (game.field.size_y - game.field.line_half_width)
    game.can_score = False  # disallow direct goal
    game.throw_in = True
    game.throw_in_ball_was_lifted = False
    #interruption('THROWIN')
    place_ball(game.ball_kick_translation)


def corner_kick(left_side):
    # set the ball in the right corner for corner kick
    sign = -1 if left_side else 1
    game.ball_kick_translation[0] = sign * (game.field.size_x - game.field.line_half_width)
    game.ball_kick_translation[1] = game.field.size_y - game.field.line_half_width if game.ball_exit_translation[1] > 0 \
        else -game.field.size_y + game.field.line_half_width
    game.can_score = True
    #interruption('CORNERKICK')
    place_ball(game.ball_kick_translation)


def goal_kick():
    # set the ball at intersection between the centerline and touchline
    game.ball_kick_translation[0] = 0
    game.ball_kick_translation[1] = game.field.size_y - game.field.line_half_width if game.ball_exit_translation[1] > 0 \
        else -game.field.size_y + game.field.line_half_width
    game.can_score = True
    #interruption('GOALKICK')
    place_ball(game.ball_kick_translation)
'''    
# --------------------------------------------------------------------------------------------------



game_controller_send.id = 0
game_controller_send.unanswered = {}
game_controller_send.sent_once = None   




# Spawn field
field_size = "junior"
field = Field(field_size)
children = supervisor.getRoot().getField('children')
children.importMFNodeFromString(-1, f'ElsirosField {{ size "{field_size}" }}')

# Spawn ball far away from field
#ball_size = 1 if field_size == 'kid' else 5
#children.importMFNodeFromString(-1, f'DEF BALL RobocupSoccerBall {{ translation 100 100 0.5 size {ball_size} }}')
children.importMFNodeFromString(-1, f'DEF BALL Ball {{ translation 100 100 0.5 }}')
game.ball_translation = supervisor.getFromDef('BALL').getField('translation')

game.side_left = game.blue.id

red_team['color'] = 'red'
blue_team['color'] = 'blue'
init_team(red_team)
init_team(blue_team)
spawn_team(red_team, game.side_left == game.blue.id, children)
spawn_team(blue_team, game.side_left == game.red.id, children)


# launch human referee
if 0:
    my_env = os.environ
    my_env["QT_PLUGIN_PATH"] = "c:\\Qt\\6.0.4\\mingw81_64\\plugins"
    my_env["QT_QPA_PLATFORM_PLUGIN_PATH"] = "c:\\Qt\\6.0.4\\mingw81_64\\plugins"
    human_referee_process = subprocess.Popen(["python", "c:\\Egor\\Starkitrobots\\Robokit\\HumanReferee\\human_referee.py"], env=my_env) # to launch without console
    #os.startfile('c:\Egor\Starkitrobots\Robokit\HumanReferee\\test.bat') # to launch with console
else:
    # launch GameController
    info('Killing all Java instances')
    os.system('taskkill /f /im java.exe')
    os.system('wmic process where \"name like \'%java%\'\" delete')
    time.sleep(1)
    info('Launching GameController')
    try:
        JAVA_HOME = os.environ['JAVA_HOME']
        try:
            GAME_CONTROLLER_HOME = os.environ['GAME_CONTROLLER_HOME']
            if not os.path.exists(GAME_CONTROLLER_HOME):
                error(f'{GAME_CONTROLLER_HOME} (GAME_CONTROLLER_HOME) folder not found.', fatal=True)
                game.controller_process = None
            else:
                path = os.path.join(GAME_CONTROLLER_HOME, 'build', 'jar', 'config', f'hl_sim_{field_size}', 'teams.cfg')
                red_line = f'{game.red.id}={red_team["name"]}\n'
                blue_line = f'{game.blue.id}={blue_team["name"]}\n'
                with open(path, 'w') as file:
                    file.write((red_line + blue_line) if game.red.id < game.blue.id else (blue_line + red_line))
                command_line = [os.path.join(JAVA_HOME, 'bin', 'java'), '-jar', 'GameControllerSimulator.jar']
                if game.minimum_real_time_factor < 1:
                    command_line.append('--fast')
                #command_line.append('--minimized')
                command_line.append('--config')
                command_line.append(game_config_file)
                if hasattr(game, 'game_controller_extra_args'):
                    for arg in game.game_controller_extra_args:
                        command_line.append(arg)
                if hasattr(game, 'use_bouncing_server') and game.use_bouncing_server:
                    command_line.append('-b')
                    command_line.append(game.host)
                    udp_bouncer_process = subprocess.Popen(["python", "udp_bouncer.py", game_config_file])
                else:
                    udp_bouncer_process = None
                game.controller_process = subprocess.Popen(command_line, cwd=os.path.join(GAME_CONTROLLER_HOME, 'build', 'jar'))
        except KeyError:
            GAME_CONTROLLER_HOME = None
            game.controller_process = None
            error('GAME_CONTROLLER_HOME environment variable not set, unable to launch GameController.', fatal=True)
    except KeyError:
        JAVA_HOME = None
        GAME_CONTROLLER_HOME = None
        game.controller_process = None
        error('JAVA_HOME environment variable not set, unable to launch GameController.', fatal=True)

'''
# connecting to human referee
try:
    game.human_referee_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    #human_referee_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    retry = 0
    while True:
        try:
            game.human_referee_socket.connect(('127.0.0.1', 7011))
            game.human_referee_socket.setblocking(False)
            break
        except socket.error as msg:
            retry += 1
            if retry <= 10:
                warning(f'Could not connect to GameController at localhost:8750: {msg}. Retrying ({retry}/10)...')
                time.sleep(retry)  # give some time to allow the GameControllerSimulator to start-up
                supervisor.step(0)
            else:
                error('Could not connect to GameController at localhost:8750.', fatal=True)
                game.human_referee_socket = None
                break
    game.human_referee_socket.setblocking(0)    
    game.human_referee_buffer = []
    game.human_referee_message = ''
    info('Connected to GameControllerSimulator at localhost:8750.')
except Exception:
    error(f"Failed connecting to GameController with the following exception {traceback.format_exc()}", fatal=True)        
'''

game.state = None

game.penalty_shootout_count = 0
game.penalty_shootout_goal = False
game.penalty_shootout_time_to_score = [None, None, None, None, None, None, None, None, None, None]
game.penalty_shootout_time_to_reach_goal_area = [None, None, None, None, None, None, None, None, None, None]
game.penalty_shootout_time_to_touch_ball = [None, None, None, None, None, None, None, None, None, None]
game.ball = supervisor.getFromDef('BALL')
game.ball_radius = 0.04 # For junior league
game.ball_kick_translation = [0, 0, game.ball_radius + game.field.turf_depth]  # initial position of ball before kick
game.ball_translation = supervisor.getFromDef('BALL').getField('translation')
game.ball_exit_translation = None
#reset_ball_touched()
game.ball_last_touch_time = 0
game.ball_first_touch_time = 0
game.ball_last_touch_time_for_display = 0
game.ball_position = [0, 0, 0]
game.ball_last_move = 0
game.real_time_multiplier = 1000 / (game.minimum_real_time_factor * time_step) if game.minimum_real_time_factor > 0 else 10
game.interruption = None
game.interruption_countdown = 0
game.interruption_step = None
game.interruption_step_time = 0
game.interruption_team = None
game.interruption_seconds = None
game.dropped_ball = False
game.overtime = False
game.finished_overtime = False
game.ready_countdown = 0  # simulated time countdown before ready state (used in kick-off after goal and dropped ball)
game.play_countdown = 0
game.in_play = None
game.throw_in = False  # True while throwing in to allow ball handling
game.throw_in_ball_was_lifted = False  # True if the throwing-in player lifted the ball
game.over = False
game.wait_for_state = 'INITIAL'
game.wait_for_sec_state = None
game.wait_for_sec_phase = None
game.font_size = 0.096
game.font = 'Lucida Console'
game.need_to_place_players_in_set = True # [Sol] For very first READY->SET transition
game_kickoff_from_json = game.kickoff
game.kickoff = game.red.id if game_kickoff_from_json == "red" else game.blue.id

setup_display()

previous_seconds_remaining = 0
last_number_of_contact_points_ball = 0

# connecting to GameController
try:
    if game.controller_process:
        info('Connecting to GameControllerSimulator at localhost:8750.')
        game.controller = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        retry = 0
        while True:
            try:
                game.controller.connect(('localhost', 8750))
                game.controller.setblocking(False)
                break
            except socket.error as msg:
                retry += 1
                if retry <= 10:
                    warning(f'Could not connect to GameController at localhost:8750: {msg}. Retrying ({retry}/10)...')
                    time.sleep(retry)  # give some time to allow the GameControllerSimulator to start-up
                    supervisor.step(0)
                else:
                    error('Could not connect to GameController at localhost:8750.', fatal=True)
                    game.controller = None
                    break
        info('Connected to GameControllerSimulator at localhost:8750.')
        try:
            game.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            game.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(game, 'use_bouncing_server') and game.use_bouncing_server:
                # In case we are using the bouncing server we have to select which interface is used because messages are not
                # broadcast
                game.udp.bind((game.host, 3838))
            else:
                game.udp.bind(('0.0.0.0', 3838))
            game.udp.setblocking(False)
        except Exception:
            error("Failed to set up UDP socket to listen to GC messages")
    else:
        info('GameControllerSimulator process not found')
        game.controller = None
except Exception:
    error(f"Failed connecting to GameController with the following exception {traceback.format_exc()}", fatal=True)

# Setting initial state on GC
try:
    update_state_display()
    info(f'Game type is {game.type}.')
    info(f'Red team is "{red_team["name"]}", playing on {"left" if game.side_left == game.red.id else "right"} side.')
    info(f'Blue team is "{blue_team["name"]}", playing on {"left" if game.side_left == game.blue.id else "right"} side.')
    game_controller_send(f'SIDE_LEFT:{game.side_left}')

    if hasattr(game, 'supervisor'):  # optional supervisor used for CI tests
        children.importMFNodeFromString(-1, f'DEF TEST_SUPERVISOR Robot {{ supervisor TRUE controller "{game.supervisor}" }}')

    if game.penalty_shootout:
        info(f'{"Red" if game.kickoff == game.red.id else "Blue"} team will start the penalty shoot-out.')
        game.phase = 'PENALTY-SHOOTOUT'
        game.ready_real_time = None
        info(f'Penalty start: Waiting {REAL_TIME_BEFORE_FIRST_READY_STATE} seconds (real-time) before going to SET')
        game.set_real_time = time.time() + REAL_TIME_BEFORE_FIRST_READY_STATE  # real time for set state (penalty-shootout)
        game_controller_send(f'KICKOFF:{game.kickoff}')
    else:
        info(f'Regular start: Waiting {REAL_TIME_BEFORE_FIRST_READY_STATE} seconds (real-time) before going to READY')
        game.ready_real_time = time.time() + REAL_TIME_BEFORE_FIRST_READY_STATE  # real time for ready state (initial kick-off)
        #kickoff() #TODO
        game_controller_send(f'KICKOFF:{game.kickoff}')
except Exception:
    error(f"Failed setting initial state: {traceback.format_exc()}", fatal=True)



game.over = False
game.human_referee_disconnected = False
last_game_controller_send_time = 0
last_game_controller_send_period_ms = 100
game.initial_state_processed = False
game.set_state_processed = False 
game.finished_state_processed = False   

while supervisor.step(time_step) != -1 and not game.over:    
    #perform_status_update() # To show realtime simulation factor if needed
    #if time_count - last_game_controller_send_time >= last_game_controller_send_period_ms:
    game_controller_send(f'CLOCK:{time_count}')
    #    last_game_controller_send_time = time_count        
    game_controller_receive()  

    #if previous_seconds_remaining != game.state.seconds_remaining:
    #    update_state_display()  
    #    previous_seconds_remaining = game.state.seconds_remaining   

    sec_state = game.state.secondary_state
    sec_phase = game.state.secondary_state_info[1]
    first_half = game.state.first_half 
    game.kickoff = game.state.kickoff_team # [Sol] GC is a master for kickoff decision in Junior league
    game.ball_position = game.ball_translation.getSFVec3f()

    # In KidSize league, this controller is autoreferee and acts as "Game state master". It  will advance through game states and send corresponding game state changes to GC
    # In Junior League, this controller is "Game state slave", The GC will switch game states according to it's rules, possibly with human interruption if needed. 
    if game.state.game_state == 'STATE_PLAYING':
        # Check for PLAYING game state time left and send FINISHED to GC if needed to swith to second half etc 
        if game.state.seconds_remaining <= 0 :
            info(f"Sending automated PLAYING -> FINISH because seconds remaining = {game.state.seconds_remaining}")
            game_controller_send('STATE:FINISH')
        '''
        if (game.interruption_countdown == 0 and game.ready_countdown == 0 and
                game.ready_real_time is None and not game.throw_in and
                (game.ball_position[1] - game.ball_radius >= game.field.size_y or
                 game.ball_position[1] + game.ball_radius <= -game.field.size_y or
                 game.ball_position[0] - game.ball_radius >= game.field.size_x or
                 game.ball_position[0] + game.ball_radius <= -game.field.size_x)):
                info(f'Ball left the field at ({game.ball_position[0]} {game.ball_position[1]} {game.ball_position[2]}) after '
                     f'being touched by {game.ball_last_touch_team} player {game.ball_last_touch_player_number}.')
                game.ball_exit_translation = game.ball_position
        '''

        update_contacts()

        if previous_seconds_remaining != game.state.seconds_remaining:
            update_state_display()   
            previous_seconds_remaining = game.state.seconds_remaining 
            
        #if (game.interruption_countdown == 0 and game.ready_countdown == 0 and
        #    game.ready_real_time is None and not game.throw_in and
        if  (game.ball_position[1] - game.ball_radius >= game.field.size_y or
                game.ball_position[1] + game.ball_radius <= -game.field.size_y or
                game.ball_position[0] - game.ball_radius >= game.field.size_x or
                game.ball_position[0] + game.ball_radius <= -game.field.size_x):
            info(f'Ball left the field at ({game.ball_position[0]} {game.ball_position[1]} {game.ball_position[2]}) after '
                    f'being touched by {game.ball_last_touch_team} player {game.ball_last_touch_player_number}.')  
            game.ball_exit_translation = game.ball_position
            scoring_team = None
            right_way = None   
            
            ball_went_out_from_left_side_of_the_field = True if game.ball_exit_translation[0] < 0 else False
            defender_touched_last = False
            # Check if ball touches defender last before it left the field
            if game.side_left == game.blue.id:
                if ball_went_out_from_left_side_of_the_field == True  and game.ball_last_touch_team == 'blue':
                    defender_touched_last = True
                if ball_went_out_from_left_side_of_the_field == False and game.ball_last_touch_team == 'red':
                    defender_touched_last = True                        
            if game.side_left == game.red.id:
                if ball_went_out_from_left_side_of_the_field == True  and game.ball_last_touch_team == 'red':
                    defender_touched_last = True
                if ball_went_out_from_left_side_of_the_field == False and game.ball_last_touch_team == 'blue':
                    defender_touched_last = True  

            info(f'ball_went_out_from_left_side_of_the_field={ball_went_out_from_left_side_of_the_field}, defender_touched_last={defender_touched_last}')

            # Check if it's a goal            
            if game.ball_exit_translation[1] < GOAL_HALF_WIDTH and \
                game.ball_exit_translation[1] > -GOAL_HALF_WIDTH and \
                game.ball_exit_translation[2] < game.field.goal_height:

                scoring_team = game.side_left  # goal left
            if game.ball_exit_translation[1] < GOAL_HALF_WIDTH and \
                game.ball_exit_translation[1] > -GOAL_HALF_WIDTH and \
                game.ball_exit_translation[2] < game.field.goal_height:
                # ball left the field between goal posts
                if game.ball_exit_translation[0] + game.ball_radius < -game.field.size_x:         
                    # score in left goal 
                    if game.side_left == game.blue.id:
                        scoring_team = game.red.id
                    else:
                        scoring_team = game.blue.id    
                if game.ball_exit_translation[0] - game.ball_radius > game.field.size_x: 
                    # score in right goal
                    if game.side_left == game.red.id:
                        scoring_team = game.red.id
                    else:
                        scoring_team = game.blue.id      
                                           
            if scoring_team:
                # It's a goal!
                goal = 'red' if scoring_team == game.blue.id else 'blue'      
                if right_way:
                    info(f'Score in {goal} goal by {game.ball_last_touch_team} player {game.ball_last_touch_player_number}') 
                else:
                    info(f'Score in {goal} goal by {game.ball_last_touch_team} player ' + f'{game.ball_last_touch_player_number} (own goal/synthetic)')    
                game_controller_send(f'SCORE:{scoring_team}')                    
                game.ball_kick_translation[0] = 0
                game.ball_kick_translation[1] = 0
                move_ball_away()

            else:
                # It's not a goal, let's do a throw-in according to the rules
                if defender_touched_last:
                    throw_in(middle_line=False, left_side=ball_went_out_from_left_side_of_the_field)
                else:
                    throw_in(middle_line=True, left_side=None)
                        
            '''

            # ball left the field through the touch line
            if game.ball_exit_translation[1] - game.ball_radius > game.field.size_y:
                game.ball_exit_translation[1] = game.field.size_y - game.field.line_half_width
                throw_in(left_side=False)
            elif game.ball_exit_translation[1] + game.ball_radius < -game.field.size_y:
                throw_in(left_side=True) 

            # ball left the field through the left goal line
            if game.ball_exit_translation[0] - game.ball_radius > game.field.size_x:
                right_way = game.ball_last_touch_team == 'red' and game.side_left == game.red.id or \
                    game.ball_last_touch_team == 'blue' and game.side_left == game.blue.id
                if game.ball_exit_translation[1] < GOAL_HALF_WIDTH and \
                    game.ball_exit_translation[1] > -GOAL_HALF_WIDTH and \
                    game.ball_exit_translation[2] < game.field.goal_height:
                    scoring_team = game.side_left  # goal
                else:
                    if right_way:
                        goal_kick()
                    else:
                        corner_kick(left_side=False)
            # ball left the field through the right goal line
            elif game.ball_exit_translation[0] + game.ball_radius < -game.field.size_x:
                right_way = game.ball_last_touch_team == 'red' and game.side_left == game.blue.id or \
                    game.ball_last_touch_team == 'blue' and game.side_left == game.red.id
                if game.ball_exit_translation[1] < GOAL_HALF_WIDTH and \
                    game.ball_exit_translation[1] > -GOAL_HALF_WIDTH and \
                    game.ball_exit_translation[2] < game.field.goal_height:
                    # goal
                    scoring_team = game.red.id if game.blue.id == game.side_left else game.blue.id
                else:
                    if right_way:
                        goal_kick()
                    else:
                        corner_kick(left_side=True)    

            if scoring_team:
                goal = 'red' if scoring_team == game.blue.id else 'blue'      
                if right_way:
                    info(f'Score in {goal} goal by {game.ball_last_touch_team} player {game.ball_last_touch_player_number}') 
                else:
                    info(f'Score in {goal} goal by {game.ball_last_touch_team} player ' + f'{game.ball_last_touch_player_number} (own goal)')    
                game.ball_kick_translation[0] = 0
                game.ball_kick_translation[1] = 0
                game_controller_send(f'SCORE:{scoring_team}')
            '''
        '''
        contact_points_ball = game.ball.getContactPoints()
        number_of_contact_points_ball = len(contact_points_ball)
        if(last_number_of_contact_points_ball != number_of_contact_points_ball):
            print(f'Ball getNumberOfContactPoints changed to {number_of_contact_points_ball}')
            for i in range(number_of_contact_points_ball):
                node = game.ball.getContactPointNode(i)
                if not node:
                    continue
                name_field = node.getField('name')
                if name_field:
                    name = name_field.getSFString()                
                    print(name)
        last_number_of_contact_points_ball = number_of_contact_points_ball
        '''
                
    elif game.state.game_state == 'STATE_READY':
        # Transition from READY to SET is done automatically by GC after proper time elapsed or manually triggered, so no need to do it here
        pass
        #if game.state.seconds_remaining <= 0:
        #    info(f"Sending automated READY -> SET because seconds remaining = {game.state.seconds_remaining}")        
        #    game_controller_send('STATE:SET')    

    elif game.state.game_state == 'STATE_SET': 
        if game.set_state_processed == False:
            game.set_state_processed = True
            info(f'Entering SET, first_half = {first_half}, sec_state = {sec_state}, sec_phase = {sec_phase}')

            if sec_state == 'STATE_NORMAL' or sec_state == 'STATE_OVERTIME':
                for number in red_team['players']:
                    if red_team['players'][str(number)]['needToBePlacedByRefereeInReady']:
                        reset_player('red', str(number), 'readyStartingPose')
                for number in blue_team['players']:
                    if blue_team['players'][str(number)]['needToBePlacedByRefereeInReady']:
                        reset_player('blue', str(number), 'readyStartingPose')
                #place_ball([0, 0, game.ball_radius + game.field.turf_depth]) # place ball at the center of the field                                  
                place_ball(game.ball_kick_translation)                        
            if sec_state == 'STATE_PENALTYSHOOT':
                set_penalty_positions()
                place_ball(game.ball_kick_translation)



        '''
        if game.ball_set_kick: 
            # place ball at the center of the field if needed
            game_interruption_place_ball(game.ball_kick_translation, enforce_distance=False) # Will also set game.ball_set_kick to False
        if game.need_to_place_players_in_set: 
            game.need_to_place_players_in_set = False       
            for number in red_team['players']:
                if red_team['players'][str(number)]['needToBePlacedByRefereeInReady']:
                    reset_player('red', str(number), 'readyStartingPose')
                    info(f'red{number} needToBePlacedByRefereeInReady=true, doing placement')
                else:
                    info(f'red{number} needToBePlacedByRefereeInReady=false')
            for number in blue_team['players']:
                if blue_team['players'][str(number)]['needToBePlacedByRefereeInReady']:
                    reset_player('blue', str(number), 'readyStartingPose')          
                    info(f'blue{number} needToBePlacedByRefereeInReady=true, doing placement')
                else:
                    info(f'blue{number} needToBePlacedByRefereeInReady=false')  
        '''                  
            
    elif game.state.game_state == 'STATE_FINISHED':
        if game.finished_state_processed == False:
            game.finished_state_processed = True        
            if sec_state == 'STATE_PENALTYSHOOT':
                game_controller_send('STATE:SET') # Set SET state on GC after penalty results in a goal not to show some stupidly freesed robots


        '''
        if game.penalty_shootout:
            if game.state.seconds_remaining <= 0:
                next_penalty_shootout()
        elif game.state.first_half:
            info("Received state FINISHED: end of first half")
            game.ready_real_time = None
        elif game.type == 'KNOCKOUT':
            if game.ready_real_time is None:
                if game.state.teams[0].score != game.state.teams[1].score:
                    game.over = True
                    break
                elif game.finished_overtime:
                    info('Beginning of penalty shout-out.')
                    game_controller_send('STATE:PENALTY-SHOOTOUT')
                    game.penalty_shootout = True
                    info(f'Going to SET in {HALF_TIME_BREAK_REAL_TIME_DURATION} seconds (real-time)')
                    game.set_real_time = time.time() + HALF_TIME_BREAK_REAL_TIME_DURATION
                elif game.overtime:                    
                    info('Beginning of the knockout first half.')
                    game_controller_send('STATE:OVERTIME-FIRST-HALF')
                    info(f'Going to READY in {HALF_TIME_BREAK_REAL_TIME_DURATION} seconds (real-time)')
                    game.ready_real_time = time.time() + HALF_TIME_BREAK_REAL_TIME_DURATION
        else:
            game.over = True
            break

        if game.penalty_shootout:  # penalty timeout was reached
            next_penalty_shootout()
            if game.over:
                break
        elif game.state.first_half:
            game_type = 'knockout ' if game.type == 'KNOCKOUT' and game.overtime else ''
            info(f'End of {game_type} first half.')
            info('Flipping sides')
            flip_sides()
            reset_teams('borderStartingPose') # [Sol] now players positioning is done when receiving SET from GC by need_to_place_players_in_set
            game.need_to_place_players_in_set = True
            game.kickoff = game.blue.id if game.kickoff == game.red.id else game.red.id
        elif game.type == 'NORMAL':
            info('End of second half.')
        elif game.type == 'KNOCKOUT':
            if not game.overtime:
                info('End of second half.')
                flip_sides()
                reset_teams('borderStartingPose') # [Sol] now players positioning is done when receiving SET from GC by need_to_place_players_in_set
                game.need_to_place_players_in_set = True
                game.kickoff = game.blue.id if game.kickoff == game.red.id else game.red.id
                game.overtime = True
            #else:
            elif game.finished_overtime == False:
                info('End of knockout second half.')
                game.finished_overtime = True
        else:
            error(f'Unsupported game type: {game.type}.', fatal=True)
        '''
                
    elif game.state.game_state == 'STATE_INITIAL':   
        if game.initial_state_processed == False: #process transition to INITIAL only once
            game.initial_state_processed = True
            info(f'Entering INITIAL, first_half = {first_half}, sec_state = {sec_state}, sec_phase = {sec_phase}')

            if sec_state == 'STATE_NORMAL' and first_half == False:
                flip_sides() # Flip sides at the beggining of main second half
            if sec_state == 'STATE_OVERTIME':
                flip_sides() # Flip sides at the beggining of each extra time

            # Place players at the border in INITIAL
            for number in red_team['players']:
                reset_player('red', str(number), 'borderStartingPose')
            for number in blue_team['players']:
                reset_player('blue', str(number), 'borderStartingPose')     

            move_ball_away()


        '''
        if game.penalty_shootout:
            if game.set_real_time <= time.time():
                info("Starting first penalty")
                set_penalty_positions()
                game_controller_send('STATE:SET')
        elif game.ready_real_time is not None:
            if game.ready_real_time <= time.time():  # initial kick-off (1st, 2nd half, extended periods, penalty shootouts)
                info('Real-time to wait elasped, moving to READY')
                game.ready_real_time = None
                #check_start_position()
                game_controller_send('STATE:READY')
        elif game.ready_countdown > 0: 
            game.ready_countdown -= 1
            if game.ready_countdown == 0:  # kick-off after goal or dropped ball
                #check_start_position()
                game_controller_send('STATE:READY')
        elif not game.state.first_half:
            game_type = ''
            if game.overtime:
                game_type = 'overtime '
            info(f'Beginning of {game_type}second half.')
            kickoff()
            info(f'Going to READY in {HALF_TIME_BREAK_REAL_TIME_DURATION} seconds (real-time)')
            game.ready_real_time = time.time() + HALF_TIME_BREAK_REAL_TIME_DURATION
        '''

    if game.state.game_state != 'STATE_INITIAL':   
        game.initial_state_processed = False
    if game.state.game_state != 'STATE_SET':   
        game.set_state_processed = False      
    if game.state.game_state != 'STATE_FINISHED':   
        game.finished_state_processed = False              

    #pass
    '''
    human_referee_receive()
    if(game.human_referee_disconnected): 
        warning(f'Human game controller GUI closed, stopping everything')
        break
    if game.human_referee_message != '':
        info(f'Received from human referee: {game.human_referee_message}') 
        human_referee_parse(game.human_referee_message) 
        game.human_referee_message = ''
        #human_referee_send('Answer')
    '''    
    time_count += time_step
    '''
    if time_count - last_time_send >= 1000: # sending time to human referee each second
        print(time_count)
        #human_referee_send("time:"+str(time_count))
        human_referee_send(str(time_count))
        last_time_send = time_count  
    '''

