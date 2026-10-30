"""Separate an offline compiler's deadline from the actual isolated game run."""
from pathlib import PurePath
class PreparationBudget:
    def __init__(self,preparation_seconds,game_seconds):
        if not 60<=preparation_seconds<=14400 or not 60<=game_seconds<=1800:raise ValueError('bounded QA preparation/game deadlines required')
        self.preparation_seconds=preparation_seconds;self.game_seconds=game_seconds;self.game_started=None
    def observe(self,seconds,processes):
        if seconds<0:raise ValueError('negative QA observation time')
        # Compiler/scanner/contract-query tools do not consume game time.
        if self.game_started is None and any(PurePath(row[2]).name in('bb-probe','bb-probe-metal','bb-game-arm64','bb-game-bbcpu')for row in processes):self.game_started=seconds
        if self.game_started is None:return 'preparation time budget'if seconds>=self.preparation_seconds else None
        return 'game time budget'if seconds-self.game_started>=self.game_seconds else None
