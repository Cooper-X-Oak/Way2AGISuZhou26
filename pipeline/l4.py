"""Compatibility entry point for the independently runnable pitch workflow."""
from pipeline.workflows.pitch import run
from pipeline.workflows.pitch.context import ANGLES,active,angles,targets,channel_for_target
from pipeline.workflows.pitch.review import references,business,inspect,check
