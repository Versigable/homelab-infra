# Size ONNX Runtime's thread pools to the CPUs this process may actually run on.
# Inside an LXC, ONNX Runtime counts the host's CPUs (16 on wolverine), so it starts one
# thread per host CPU on the container's few cores and fails to pin them. Python imports
# this file at startup when its directory is on PYTHONPATH (compose mounts it read-only).
import os

try:
    import onnxruntime as _ort
except ImportError:
    _ort = None

if _ort is not None:
    _cpus = len(os.sched_getaffinity(0))
    _orig_init = _ort.InferenceSession.__init__

    def _init(self, path_or_bytes, sess_options=None, *args, **kwargs):
        opts = sess_options if sess_options is not None else _ort.SessionOptions()
        if opts.intra_op_num_threads == 0:
            opts.intra_op_num_threads = _cpus
        if opts.inter_op_num_threads == 0:
            opts.inter_op_num_threads = 1
        _orig_init(self, path_or_bytes, opts, *args, **kwargs)

    _ort.InferenceSession.__init__ = _init
