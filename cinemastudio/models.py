"""Shared dataclasses for screenplay + shot list."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Character(BaseModel):
    name: str
    description: str = Field(
        ..., description="Locked physical description: age, build, hair, eyes, wardrobe, distinguishing marks."
    )


class Shot(BaseModel):
    index: int
    scene: int
    description: str = Field(..., description="What happens in the shot, plain language.")
    motion_prompt: str = Field(
        ..., description="Camera + subject motion for Seedance, 1-2 sentences."
    )
    keyframe_prompt: str = Field(
        ..., description="Detailed photorealistic prompt for the still keyframe image."
    )
    audio_prompt: str = Field(
        default="",
        description="Diegetic sounds, ambience, optional dialogue/music cues for Seedance native audio.",
    )
    duration_seconds: int = 15
    character_names: list[str] = Field(default_factory=list)


class Scene(BaseModel):
    index: int
    title: str
    location: str
    time_of_day: str
    mood: str
    summary: str
    shot_indices: list[int]


class Screenplay(BaseModel):
    logline: str
    title: str
    style: str = Field(..., description="Visual style note, e.g. 'shot on Arri Alexa, 35mm, golden hour, naturalistic.'")
    aspect_ratio: str = "16:9"
    target_minutes: float = 1.0
    characters: list[Character]
    scenes: list[Scene]
    shots: list[Shot]
