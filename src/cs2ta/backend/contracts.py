"""Versioned JSON boundary for local and future remote parsing executors."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RoundId = Annotated[int, Field(strict=True, ge=1)]


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["parse-task-v1"] = "parse-task-v1"
    kind: Literal["ingest", "positions", "utility"]
    source_uri: str = Field(min_length=1)
    dataset_uri: str | None = None
    round_ids: list[RoundId] = Field(default_factory=list, max_length=20)
    tick_step: int = Field(default=16, strict=True, ge=1, le=1024)

    @model_validator(mode="after")
    def validate_task(self):
        if self.kind == "ingest" and self.dataset_uri is not None:
            raise ValueError("导入任务不接受已有数据集。")
        if self.kind != "ingest" and not self.dataset_uri:
            raise ValueError("轨迹任务需要已有数据集。")
        if (self.kind == "positions") != bool(self.round_ids):
            raise ValueError("仅位置任务需要选择 1–20 个回合。")
        if len(set(self.round_ids)) != len(self.round_ids):
            raise ValueError("回合不能重复。")
        return self


class ParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["parse-result-v1"] = "parse-result-v1"
    status: Literal["succeeded"] = "succeeded"
    dataset_uri: str
    manifest: dict
    rounds: list[dict]
    row_counts: dict[str, int] = Field(default_factory=dict)
    diagnostics: dict = Field(default_factory=dict)
