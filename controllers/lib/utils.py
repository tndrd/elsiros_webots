import sys
import json
import os
from types import SimpleNamespace
from controller import Supervisor
import traceback
import socket


class ElsirosConfigInterface():
    def __init__(self, supervisor, config_filename="game.json"):
        self.supervisor = supervisor
        self.game_config_path, self.game = self._load_game_config(config_filename)

    def _load_game_config(self, filename):
        # determine configuration file name
        game_config_file = os.environ['WEBOTS_ROBOCUP_GAME'] if 'WEBOTS_ROBOCUP_GAME' in os.environ \
            else os.path.join(os.getcwd(), filename)
        if not os.path.isfile(game_config_file):
            print(f'Cannot read {game_config_file} game config file.', file=sys.stderr)

        # read configuration files
        game = None
        with open(game_config_file) as json_file:
            game = json.loads(json_file.read(), object_hook=lambda d: SimpleNamespace(**d))
        return game

    def enable_recording(self):
        game = self.game
        supervisor = self.supervisor
        if hasattr(game, 'record_simulation'):
            try:
                if game.record_simulation.endswith(".html"):
                    supervisor.animationStartRecording(game.record_simulation)
                elif game.record_simulation.endswith(".mp4"):
                    supervisor.movieStartRecording(game.record_simulation, width=1280, height=720, codec=0, quality=100,
                                                acceleration=1, caption=False)
                    if supervisor.movieFailed():
                        raise RuntimeError("Failed to Open Movie")
                else:
                    raise RuntimeError(f"Unknown extension for record_simulation: {game.record_simulation}")
            except Exception:
                print(f"Failed to start recording with exception: {traceback.format_exc()}", file=sys.stderr)

    def stop_recording(self):
        game = self.game
        supervisor = self.supervisor
        if hasattr(game, 'record_simulation'):
            if game.record_simulation.endswith(".html"):
                supervisor.animationStopRecording()
            elif game.record_simulation.endswith(".mp4"):
                print("Starting encoding", file=sys.stderr)
                supervisor.movieStopRecording()
                while not supervisor.movieIsReady():
                    time_step = int(supervisor.getBasicTimeStep())
                    supervisor.step(time_step)
                print("Encoding finished", file=sys.stderr)

class BaseLogger():
    def __init__(self, dst): pass
    def flush(self): pass
    def write(self, data): pass
    def close(self): pass

class TextLogger(BaseLogger):
    def __init__(self, filename):
        self.f = open(filename, 'w')

    def flush(self): return self.f.flush()

    def write(self, data):
        self.f.write(data)

    def close(self):
        self.f.close()

class SocketLogger(BaseLogger):
    def __init__(self, port):
        self.sock = socket.socket()
        print(f"Connecting to logger on port {port}...", file=sys.stderr)
        self.sock.connect(('localhost', port))
        print(f"Connected to logger on port {port}", file=sys.stderr)

    def write(self, data):
        try:
            self.sock.send(data.encode("utf-8"))
        except Exception as inst:
            print(f"Exception while sending data to launcher: {inst.args}", file=sys.stderr)

    def close(self):
        self.write("END OF LOGGING\n")
        self.sock.close()

class ComplexLogger(BaseLogger):
    def __init__(self, loggers):
        self.loggers = [logger[0](logger[1]) for logger in loggers]
    
    def flush(self):
        for logger in self.loggers: logger.flush()

    def write(self, data):
        for logger in self.loggers: logger.write(data)

    def write(self, data):
        for logger in self.loggers: logger.close()

def get_logger(filename):
    
    loggers = [(TextLogger, filename)]
    
    launcher_port = os.environ.get('ELSIROS_LAUNCHER_PORT')

    if launcher_port is not None:
        loggers.append((SocketLogger, int(launcher_port)))

    return ComplexLogger(loggers)
