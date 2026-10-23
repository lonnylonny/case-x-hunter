"""网页/API 的非密钥实例设置；CLI 实验参数保持独立。"""

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    top_k: StrictInt = Field(gt=0, le=10)
    max_tokens: StrictInt

    @field_validator("max_tokens")
    @classmethod
    def _allowed_output_limit(cls, value: int) -> int:
        if value not in (2048, 4096):
            raise ValueError("输出上限仅支持 2048 或 4096")
        return value


DEFAULT_SETTINGS = RuntimeSettings(top_k=4, max_tokens=4096)
