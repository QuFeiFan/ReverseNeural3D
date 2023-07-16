import os
from imageio import imread
from skimage.transform import resize
from torchvision.transforms.functional import resize as resize_tensor
import cv2
import random
import numpy as np
import torch
import utils
import matplotlib.pyplot as plt
import h5py

cv2.setNumThreads(0)

# Check for endianness, based on Daniel Scharstein's optical flow code.
# Using little-endian architecture, these two should be equal.
# TAG_FLOAT = 202021.25
# TAG_CHAR = 'PIEH'


# type is a string that appeared in the image file name, should be chosen from 'color' or 'depth'
def get_image_filenames(dir, keyword=None, type=None):
    """Returns all files in the input directory dir that are images"""
    image_types = ('jpg', 'jpeg', 'tiff', 'tif', 'png', 'bmp', 'gif', 'exr', 'dpt', 'hdf5','webp','pfm', 'pt')
    if isinstance(dir, str):
        files = os.listdir(dir)
        exts = (os.path.splitext(f)[1] for f in files)
        if keyword != None:
            images = [os.path.join(dir, f)
                        for e, f in zip(exts, files)
                        if e[1:] in image_types and keyword in f]
        else:
            images = [os.path.join(dir, f)
                        for e, f in zip(exts, files)
                        if e[1:] in image_types]
        return images
    elif isinstance(dir, list):
        # Suppport multiple directories (randomly shuffle all)
        images = []
        for folder in dir:
            files = os.listdir(folder)
            exts = (os.path.splitext(f)[1] for f in files)
            if keyword != None:
                images_in_folder = [os.path.join(folder, f)
                                    for e, f in zip(exts, files)
                                    if e[1:] in image_types and keyword in f]
            else:
                images_in_folder = [os.path.join(folder, f)
                                    for e, f in zip(exts, files)
                                    if e[1:] in image_types]
            images = [*images, *images_in_folder]
        return images


def resize_keep_aspect(image, target_res, pad=False, lf=False, pytorch=False):
    """Resizes image to the target_res while keeping aspect ratio by cropping

    image: an 3d array with dims [channel, height, width]
    target_res: [height, width]
    pad: if True, will pad zeros instead of cropping to preserve aspect ratio
    """
    im_res = image.shape[-2:]

    # finds the resolution needed for either dimension to have the target aspect
    # ratio, when the other is kept constant. If the image doesn't have the
    # target ratio, then one of these two will be larger, and the other smaller,
    # than the current image dimensions
    resized_res = (int(np.ceil(im_res[1] * target_res[0] / target_res[1])),
                   int(np.ceil(im_res[0] * target_res[1] / target_res[0])))

    # only pads smaller or crops larger dims, meaning that the resulting image
    # size will be the target aspect ratio after a single pad/crop to the
    # resized_res dimensions
    if pad:
        image = utils.pad_image(image, resized_res, pytorch=False)
    else:
        image = utils.crop_image(image, resized_res, pytorch=False, lf=lf)

    # switch to numpy channel dim convention, resize, switch back
    if lf or pytorch:
        image = resize_tensor(image, target_res)
        return image
    else:
        image = np.transpose(image, axes=(1, 2, 0))
        image = resize(image, target_res, mode='reflect')
        return np.transpose(image, axes=(2, 0, 1))


def pad_crop_to_res(image, target_res, pytorch=False):
    """Pads with 0 and crops as needed to force image to be target_res

    image: an array with dims [..., channel, height, width]
    target_res: [height, width]
    """
    return utils.crop_image(utils.pad_image(image,
                                            target_res, pytorch=pytorch, stacked_complex=False),
                            target_res, pytorch=pytorch, stacked_complex=False)




class hypersim_TargetLoader(torch.utils.data.IterableDataset):
    """Loads target amp/mask tuples for phase optimization

    Class initialization parameters
    -------------------------------
    :param data_path:
    :param channel:
    :param image_res:
    :param roi_res:
    :param crop_to_roi:
    :param shuffle:
    :param virtual_depth_planes:
    :param return_type: 'image_mask_id' or 'image_depth_id'

    """

    def __init__(self, data_path, channel=None,
                 image_res=(800, 1280), roi_res=(700, 1190),
                 crop_to_roi=False, shuffle=False, scale_vd_range=True,
                 virtual_depth_planes=None, return_type='image_mask_id',
                 random_seed=None, slice=(0, 1)):
        """ initialization """
        if isinstance(data_path, str) and not os.path.isdir(data_path):
            raise NotADirectoryError(f'Data folder: {data_path}')

        self.data_path = data_path
        self.channel = channel
        self.roi_res = roi_res
        self.image_res = image_res
        self.shuffle = shuffle
        self.virtual_depth_planes = virtual_depth_planes
        self.scale_vd_range = scale_vd_range
        self.vd_min = 0.01
        self.vd_max = max(self.virtual_depth_planes)
        self.return_type = return_type
        self.slice = slice
        self.random_seed = random_seed
        
        # self.im_names = get_image_filenames(dir = os.path.join(self.data_path, 'scene_cam_00_final_hdf5'), keyword = 'color')
        if isinstance(self.data_path, str):
            self.im_names = get_image_filenames(dir = os.path.join(self.data_path, 'scene_cam_00_final_preview'), keyword = 'color')
            self.depth_names = get_image_filenames(dir = os.path.join(self.data_path, 'scene_cam_00_geometry_hdf5'), keyword = 'depth_meters')
        elif isinstance(self.data_path, list):
            self.im_names = get_image_filenames(dir = [os.path.join(path, 'scene_cam_00_final_preview') for path in self.data_path], keyword = 'color')
            self.depth_names = get_image_filenames(dir = [os.path.join(path, 'scene_cam_00_geometry_hdf5') for path in self.data_path], keyword = 'depth_meters')
        
        length = len(self.im_names)
        assert(len(self.im_names) == len(self.depth_names))

        self.im_names.sort()
        self.depth_names.sort()

        self.order = list((i) for i in range(length))
        if self.random_seed != None:
            random.Random(self.random_seed).shuffle(self.order)
        self.order = self.order[round(self.slice[0]*length):round(self.slice[1]*length)]

    def __iter__(self):
        self.ind = 0
        return self

    def __len__(self):
        return len(self.order)

    def __next__(self):
        if self.ind < len(self.order):
            img_idx = self.order[self.ind]
            self.ind += 1
            if self.return_type == 'image_mask_id':
                return self.load_image_mask(img_idx)
            elif self.return_type == 'image_depth_id':
                return self.load_image_depth(img_idx)
        else:
            raise StopIteration

    def load_image(self, filenum):
        # with h5py.File(self.im_names[filenum], 'r') as im_file:
        #     # im_file = h5py.File(self.im_names[filenum])
        #     im = im_file['dataset'][:]
        
        # im = im[..., self.channel, np.newaxis]
        # im = utils.im2float(im, dtype=np.float64)
        # im = np.transpose(im, axes=(2, 0, 1))
        
        im = imread(self.im_names[filenum])
        im = im[..., self.channel, np.newaxis]
        im = utils.im2float(im, dtype=np.float64)  # convert to double, max 1
        # linearize intensity and convert to amplitude
        im = utils.srgb_gamma2lin(im)
        im = np.sqrt(im)  # to amplitude

        # move channel dim to torch convention
        im = np.transpose(im, axes=(2, 0, 1))
        
        im = resize_keep_aspect(im, self.roi_res)
        im = pad_crop_to_res(im, self.image_res)
        
        path = os.path.splitext(self.im_names[filenum])[0]

        return (torch.from_numpy(im).float(),
                None,
                os.path.split(path)[1].split('_')[-1])
        
    def depth_convert(self, depth):
        # NaN to inf
        depth[depth==0] = 1.0
        # convert to double
        # depth = depth.double()
        
        # meter to diopter conversion
        # depth = 1 / (depth + 1e-20)        
        return depth

    def load_depth(self, filenum):
        distance_path = self.depth_names[filenum]
        with h5py.File(distance_path, 'r') as dist_file:
            # dist_file = h5py.File(distance_path)
            distance = dist_file['dataset'][:]

        distance = distance.astype(np.float64)  # convert to double, max 1
        
        # NaN to inf
        distance[np.isnan(distance)] = 100

        # distance = self.depth_convert(distance)
        distance = 1 / (distance + 1e-20)  # meter to diopter conversion
        
        

        # convert from numpy array to pytorch tensor, shape = (1, original_h, original_w)
        distance = torch.from_numpy(distance.copy()).float().unsqueeze(0)
        
        distance = resize_keep_aspect(distance, self.roi_res, pytorch=True)
        distance = pad_crop_to_res(distance, self.image_res, pytorch=True)
        
        # perform scaling in meters
        if self.scale_vd_range:
            distance = distance - distance.min()
            distance = (distance / distance.max()) * (self.vd_max - self.vd_min)
            distance = distance + self.vd_min

        # check nans
        if (distance.isnan().any()):
            print("Found Nans in target depth!")
            min_substitute = self.vd_min * torch.ones_like(distance)
            distance = torch.where(distance.isnan(), min_substitute, distance)

        path = os.path.splitext(self.depth_names[filenum])[0]

        return (distance.float(),
                None,
                os.path.split(path)[1].split('_')[-1])

    def load_image_mask(self, filenum):
        img_none_idx = self.load_image(filenum)
        depth_none_idx = self.load_depth(filenum)
        mask = utils.decompose_depthmap(depth_none_idx[0], self.virtual_depth_planes)
        return (img_none_idx[0], mask, img_none_idx[-1])
    
    def load_image_depth(self, filenum):
        img_none_idx = self.load_image(filenum)
        depth_none_idx = self.load_depth(filenum)
        return (img_none_idx[0], depth_none_idx[0], img_none_idx[-1])


if __name__ == '__main__':
    img_list = get_image_filenames('/media/datadrive/hypersim/ai_001_001/images/scene_cam_00_final_hdf5', keyword='color')
    dist_list = get_image_filenames('/media/datadrive/hypersim/ai_001_001/images/scene_cam_00_geometry_hdf5', keyword='depth_meters')
    
    virtual_depth_planes=[0.0, 0.08417508417508479, 0.14124293785310726, 0.24299599771297942, 0.3171856978085348, 0.4155730533683304, 0.5319148936170226, 0.6112104949314254]
    hypersim = hypersim_TargetLoader(data_path='/media/datadrive/hypersim/ai_001_001/images', 
                            channel=1, 
                            shuffle=False, 
                            virtual_depth_planes=[virtual_depth_planes[idx] for idx in [0,3,5]]
                            # return_type='image_depth_id',
                            )
    
    for i, target in enumerate(hypersim):
        
        target_amp, target_mask, target_idx = target
        
        # plt.hist(target_depth.numpy().ravel())
        # plt.savefig('test.png')

        pass
    
    pass