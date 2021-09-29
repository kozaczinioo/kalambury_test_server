import asyncio
import json
import os
import random
import threading
from datetime import datetime, timedelta
from typing import List

import requests
from fuzzywuzzy import fuzz

from .connection import Connection
from .models import PlayerGuess, GuessResult
from .server_errors import GameNotStarted

CLUES = ['pies', 'kot', 'Ala', "Owocowe czwartki", "Homeoffice", "Pan kanapka"]


class Room:
    def __init__(self, room_id):
        self.id = room_id
        self.active_connections: List[Connection] = []
        self.is_game_on = False
        self.game_data: bytes = bytearray()
        self.whos_turn: int = 0
        self.clue = None
        self.timeout = self.get_timeout()
        self.timer = threading.Timer(self.timeout, self.next_person_async)

    def get_timeout(self):
        try:
            timeout = float(os.path.join(os.getenv('TIMEOUT_SECONDS')))
        except TypeError:
            timeout = 15  # if theres no env var
        return timeout

    def next_person_async(self):
        asyncio.run(self.restart_game())

    async def append_connection(self, connection):
        self.active_connections.append(connection)
        self.export_room_status()
        if len(self.active_connections) > 1 and self.is_game_on is False:
            await self.start_game()

    async def remove_connection(self, connection_with_given_ws):
        self.active_connections.remove(connection_with_given_ws)
        self.export_room_status()
        if len(self.active_connections) <= 1:
            await self.end_game()
        if connection_with_given_ws.player.id == self.whos_turn:
            await self.restart_game()

    async def handle_players_guess(self, player_guess: PlayerGuess, score_thresh=60):
        score = fuzz.ratio(player_guess.message, self.clue)

        if not self.is_game_on:
            raise GameNotStarted
        if player_guess.message.lower() == self.clue.lower():
            winning_clue = self.clue
            drawer = str(self.whos_turn)
            await self.restart_game()
            return GuessResult(status="WIN", clue=winning_clue, winner=player_guess.player_id, drawer=drawer)

        elif score > score_thresh:
            return GuessResult(status="IS_CLOSE")
        else:
            return GuessResult(status="MISS")

    async def broadcast_json(self):
        for connection in self.active_connections:
            gs = self.get_game_state(connection.player.id)
            await connection.ws.send_text(gs)

    async def restart_game(self):
        await self.start_game()

    async def start_game(self):
        self.game_data = bytearray()
        self.is_game_on = True
        self.whos_turn = self.draw_random_player_id()
        self.clue = random.choice(CLUES)
        self.restart_timer()
        await self.broadcast_json()

    async def end_game(self):
        self.is_game_on = False
        self.whos_turn = 0
        self.clue = None
        await self.broadcast_json()

    def get_game_state(self, client_id) -> str:
        if client_id == self.whos_turn:
            game_state = {
                "is_game_on": self.is_game_on,
                "whos_turn": self.whos_turn,
                "sequence_to_guess": self.clue,
                "game_data": self.game_data.decode('ISO-8859-1'),
                "timestamp": self.timestamp.isoformat(),
            }
        else:
            game_state = {
                "is_game_on": self.is_game_on,
                "whos_turn": self.whos_turn,
                "drawer": self.get_drawer_nick(),
                "game_data": self.game_data.decode('ISO-8859-1')
            }
            if self.is_game_on is True:
                game_state["timestamp"] = self.timestamp.isoformat()

        return json.dumps(game_state)

    def draw_random_player_id(self):
        return random.choice(
            [connection.player.id for connection in self.active_connections])

    def get_stats(self):
        return {"is_game_on": self.is_game_on,
                "whos turn": self.whos_turn,
                "number_of_connected_players": len(self.active_connections),
                "clue": self.clue}

    def restart_timer(self):
        self.timer.cancel()
        self.timer = threading.Timer(self.timeout, self.next_person_async)
        self.timer.start()
        self.timestamp = datetime.now() + timedelta(0, self.timeout)

    def export_score(self):
        # do not need this
        ...

    def export_room_status(self):
        try:
            players_in_game = []
            for player in self.active_connections:
                players_in_game.append(player.player.id)
            result = requests.post(
                url=os.path.join(os.getenv('EXPORT_RESULTS_URL'), "rooms/update-room-status"),
                json=dict(roomId=self.id, activePlayers=players_in_game,
                          currentDrawer=self.whos_turn if self.whos_turn != 0 else None))
            if result.status_code == 200:
                print("export succesfull")
            else:
                print("export failed: ", result.text, result.status_code)
        except Exception as e:
            print(e.__class__.__name__)
            print("failed to get EXPORT_RESULTS_URL env var")

    def get_drawer_nick(self):
        try:
            player_nick = next(
                connection.player.nick for connection in self.active_connections if connection.player.id == self.whos_turn)
            drawer = "drawer: " + str(player_nick)
        except StopIteration:
            drawer = " "
        return drawer
