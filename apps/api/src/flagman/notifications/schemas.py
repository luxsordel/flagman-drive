from pydantic import BaseModel, ConfigDict, Field

from flagman.notifications.domain import (
    MANDATORY_CATEGORIES,
    PUSH_TOKEN_MAX_LENGTH,
    PUSH_TOKEN_RE,
    NotificationCategory,
    NotificationPreferences,
    PushPlatform,
)


class PushDeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        max_length=PUSH_TOKEN_MAX_LENGTH,
        pattern=f"^{PUSH_TOKEN_RE.pattern}$",
        description="The Expo push token of the app on this phone.",
        examples=["ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"],
    )
    platform: PushPlatform


class NotificationCategoryOut(BaseModel):
    category: NotificationCategory
    enabled: bool
    mandatory: bool = Field(description="Trip and payment notifications cannot be switched off.")


class NotificationPreferencesOut(BaseModel):
    categories: list[NotificationCategoryOut]

    @classmethod
    def of(cls, preferences: NotificationPreferences) -> "NotificationPreferencesOut":
        return cls(
            categories=[
                NotificationCategoryOut(
                    category=category,
                    enabled=preferences.is_enabled(category),
                    mandatory=category in MANDATORY_CATEGORIES,
                )
                for category in NotificationCategory
            ]
        )


class NotificationPreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: dict[NotificationCategory, bool] = Field(
        description="Only the categories to change; the others stay as they are.",
        examples=[{"reservation": False}],
    )
