import contextlib
import os
from typing import Dict, Optional, Sequence

from stable_baselines.common.misc_util import mpi_rank_or_zero
import stable_baselines.logger as sb_logger


def _sb_logger_configure_replacement(*args, **kwargs):
  raise RuntimeError("Shouldn't call stable_baselines.logger.configure "
                     "once imitation.logger.configure() has been called")


def _sb_logger_reset_replacement():
  raise NotImplementedError("reset() while using imitation.configure()")


_in_accumul_context = False
_configured = False
_format_strs = None


def configure(folder: str, format_strs: Optional[Sequence[str]] = None) -> None:
  """Configure the Stable Baselines logger to be `accumulate()`-compatible.

  Args:
      folder: Argument from `stable_baselines.logger.configure`.
      format_strs: Argument from `stable_baselines.logger.configure`.
  """
  global _configured, _format_strs
  assert not _in_accumul_context
  _configured = True
  _format_strs = format_strs

  sb_logger.configure = _sb_logger_configure_replacement
  sb_logger.reset = _sb_logger_reset_replacement

  output_formats = _build_output_formats(folder, format_strs)
  sb_logger.Logger.DEFAULT = sb_logger.Logger(folder, output_formats)
  sb_logger.Logger.CURRENT = sb_logger.Logger.DEFAULT
  sb_logger.log('Logging to %s' % folder)


@contextlib.contextmanager
def accumulate(subdir_name: str):
  """Temporarily redirect logkv() to a different logger and auto-track kvmeans.

  Within this context, the default logger is swapped out for a special logger
  in directory `"{current_logging_dir}/accumul_raw/{subdir_name}"`.

  The special logger's `stable_baselines.logger.logkv(key, val)`, in addition
  to tracking its own logs, also forwards the log to the default logger's
  `.logkv_mean()` under the key `accumul_mean/{subdir_name}/{key}`.

  After the context exits, these means can be dumped as usual using
  `stable_baselines.logger.dumpkvs()`.

  Args:
    subdir_name: Chooses the logger subdirectories and temporary logger.
  """
  global _in_accumul_context
  assert _configured
  assert not _in_accumul_context
  _in_accumul_context = True

  try:
    sb_logger.Logger.CURRENT = _AccumulatingLogger.from_subdir(subdir_name)
    yield
  finally:
    # Switch back to default logger.
    sb_logger.Logger.CURRENT = sb_logger.Logger.DEFAULT
    _in_accumul_context = False


class _AccumulatingLogger(sb_logger.Logger):

  _cached_loggers: Dict[str, "_AccumulatingLogger"] = {}

  def __init__(self,
               folder,
               output_formats,
               *,
               subdir: str):
    """Like Logger, except also accumulates logkv_mean on target_logger."""
    super().__init__(folder, output_formats)
    self.subdir = subdir

  def logkv(self, key, val):
    super().logkv(key, val)
    accumulate_key = os.path.join("accumul_mean", self.subdir, key)
    sb_logger.Logger.DEFAULT.logkv_mean(accumulate_key, val)

  @classmethod
  def from_subdir(cls, subdir: str):
    if subdir in cls._cached_loggers:
      return cls._cached_loggers[subdir]
    else:
      default_log = sb_logger.Logger.DEFAULT
      folder = os.path.join(default_log.dir, "accumul_raw", subdir)
      os.makedirs(folder, exist_ok=True)
      output_formats = _build_output_formats(folder, _format_strs)
      result = cls(folder, output_formats, subdir=subdir)
      cls._cached_loggers[subdir] = result
      return result


def _build_output_formats(folder, format_strs):
  assert mpi_rank_or_zero() == 0
  os.makedirs(folder, exist_ok=True)
  if format_strs is None:
    format_strs = ['stdout', 'log', 'csv']
  output_formats = [sb_logger.make_output_format(f, folder)
                    for f in format_strs]
  return output_formats
