from pydantic import BaseModel


class SignalOut(BaseModel):
    asset: str
    direction: str
    start_from: float
    start_to: float
    take_profit: float
    stop_loss: float
    priority: str
    status: str
    reason: str
    timeframe: str = "4H/1D"
    invalidation_rule: str | None = None


class GenerateSignalsResponse(BaseModel):
    signals: list[SignalOut]
