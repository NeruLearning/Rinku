from torch.utils.cpp_extension import CUDA_HOME
print(CUDA_HOME)

import torch
print(torch.version.cuda)