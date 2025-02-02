"""
Copyright Snap Inc. 2021. This sample code is made available by Snap Inc. for informational purposes only.
No license, whether implied or otherwise, is granted in or to such code (including any rights to copy, modify,
publish, distribute and/or commercialize such code), unless you have entered into a separate agreement for such rights.
Such code is provided as-is, without warranty of any kind, express or implied, including any warranties of merchantability,
title, fitness for a particular purpose, non-infringement, or that such code is free of defects, errors or viruses.
In no event will Snap Inc. be liable for any damages or losses of any kind arising from the sample code or your use thereof.
"""
import os.path as osp
import json
import random

from PIL import Image
import numpy as np
import torch
import torch.nn as nn
import torch.utils.data as data
import h5py
import warnings
import pickle
from torchvision.datasets.video_utils import VideoClips

IMG_EXTENSIONS = ['.jpg', '.JPG', '.jpeg', '.JPEG', '.png', '.PNG']


def is_image_file(filename):
    return any(filename.endswith(extension) for extension in IMG_EXTENSIONS)


def preprocess(image):
    # [0, 1] => [-1, 1]
    img = image * 2.0 - 1.0
    img = np.transpose(img, (2, 0, 1))
    img = torch.from_numpy(img)
    return img


import math
import torch.nn.functional as F
def preprocess(video, resolution):
    # video: THWC, {0, ..., 255}
    video = video.permute(0, 3, 1, 2).float() # TCHW
    t, c, h, w = video.shape

    # scale shorter side to resolution
    scale = resolution / min(h, w)
    if h < w:
        target_size = (resolution, math.ceil(w * scale))
    else:
        target_size = (math.ceil(h * scale), resolution)
    video = F.interpolate(video, size=target_size, mode='bilinear',
                          align_corners=False)
    
    scale = tuple([t / r for t, r in zip(target_size, (h, w))])

    # center crop
    t, c, h, w = video.shape
    w_start = (w - resolution) // 2
    h_start = (h - resolution) // 2
    video = video[:, :, h_start:h_start + resolution, w_start:w_start + resolution]

    video = 2 * video / 255. - 1

    return video

    
class SomethingSomething(data.Dataset):
    def __init__(self, opt):
        super().__init__()
        self.opt = opt
        self.resolution = opt.video_frame_size
        self.sequence_length = opt.n_frames_G

        self.root = opt.dataroot
        video_ids = json.load(open(osp.join(self.root, 'train_subset.json'), 'r'))
        to_exclude = json.load(open(osp.join(self.root, 'exclude.json'), 'r'))
        to_exclude = set(to_exclude)
        video_ids = list(filter(lambda vid: vid not in to_exclude, video_ids))

        files = [osp.join(self.root, '20bn-something-something-v2', f'{vid}.webm')
                 for vid in video_ids]
        
        warnings.filterwarnings('ignore')
        cache_file = osp.join(self.root, 'train_metadata_4.pkl')
        metadata = pickle.load(open(cache_file, 'rb'))
        clips = VideoClips(files, self.sequence_length, _precomputed_metadata=metadata)
        self._clips = clips
    
    def __len__(self):
        return self._clips.num_clips()
    
    def __getitem__(self, idx):
        video = self._clips.get_clip(idx)[0]
        video = preprocess(video, self.resolution)
        return {'real_img': video}

    
class HDF5Dataset(data.Dataset):
    """ Generic dataset for data stored in h5py as uint8 numpy arrays.
    Reads videos in {0, ..., 255} and returns in range [-0.5, 0.5] """
    def __init__(self, opt):
        """
        Args:
            args.data_path: path to the pickled data file with the
                following format:
                {
                    'train_data': [B, H, W, 3] np.uint8,
                    'train_idx': [B], np.int64 (start indexes for each video)
                    'test_data': [B', H, W, 3] np.uint8,
                    'test_idx': [B'], np.int64
                }
            args.sequence_length: length of extracted video sequences
        """
        super().__init__()
        self.opt = opt
        self.sequence_length = opt.n_frames_G * opt.time_step
        self.resolution = opt.video_frame_size
        self.frame_skip = opt.time_step

        # read in data
        self.data_file = opt.dataroot
        self.data = h5py.File(self.data_file, 'r')
        self._images = self.data[f'train_data']
        self._idx = self.data[f'train_idx'][:]

        self.size = len(self._idx)

    def __getstate__(self):
        state = self.__dict__
        #state['data'].close()
        state['data'] = None
        state['_images'] = None

        return state

    def __setstate__(self, state):
        self.__dict__ = state
        self.data = h5py.File(self.data_file, 'r')
        self._images = self.data[f'train_data']

    def __len__(self):
        return self.size

    def __getitem__(self, idx):
        start = self._idx[idx]
        end = self._idx[idx + 1] if idx < len(self._idx) - 1 else len(self._images)
        if end - start > self.sequence_length:
            start = start + np.random.randint(low=0, high=end - start - self.sequence_length)
        assert start < start + self.sequence_length <= end, f'{start}, {end}'
        video = torch.tensor(self._images[start:start + self.sequence_length]) 
        video = video[::self.frame_skip]
        video = preprocess(video, self.resolution)

        return {'real_img': video}


class VideoDataset(data.Dataset):
    def load_video_frames(self, dataroot):
        data_all = []
        frame_list = os.walk(dataroot)
        for _, meta in enumerate(frame_list):
            root = meta[0]
            frames = sorted(meta[2], key=lambda item: int(item.split('.')[0]))
            frames = [
                os.path.join(root, item) for item in frames
                if is_image_file(item)
            ]
            if len(frames) > self.opt.n_frames_G * self.opt.time_step:
                data_all.append(frames)
        self.video_num = len(data_all)
        return data_all

    def __init__(self, opt):
        self.opt = opt
        self.data_all = self.load_video_frames(opt.dataroot)

    def __getitem__(self, index):
        batch_data = self.getTensor(index)
        return_list = {'real_img': batch_data}

        return return_list

    def getTensor(self, index):
        n_frames = self.opt.n_frames_G

        video = self.data_all[index]
        video_len = len(video)

        n_frames_interval = n_frames * self.opt.time_step
        start_idx = random.randint(0, video_len - 1 - n_frames_interval)
        img = Image.open(video[0])
        h, w = img.height, img.width

        if h > w:
            half = (h - w) // 2
            cropsize = (0, half, w, half + w)  # left, upper, right, lower
        elif w > h:
            half = (w - h) // 2
            cropsize = (half, 0, half + h, h)

        images = []
        for i in range(start_idx, start_idx + n_frames_interval,
                       self.opt.time_step):
            path = video[i]
            img = Image.open(path)

            if h != w:
                img = img.crop(cropsize)

            img = img.resize(
                (self.opt.video_frame_size, self.opt.video_frame_size),
                Image.ANTIALIAS)
            img = np.asarray(img, dtype=np.float32)
            img /= 255.
            img_tensor = preprocess(img).unsqueeze(0)
            images.append(img_tensor)

        video_clip = torch.cat(images)
        return video_clip

    def __len__(self):
        return self.video_num

    def name(self):
        return 'VideoDataset'
