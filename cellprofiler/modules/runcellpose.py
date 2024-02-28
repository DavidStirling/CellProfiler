#################################
#
# Imports from useful Python libraries
#
#################################

import numpy
import os
import skimage
import logging

#################################
#
# Imports from CellProfiler
#
##################################

from cellprofiler_core.image import Image
from cellprofiler_core.module.image_segmentation import ImageSegmentation
from cellprofiler_core.object import Objects
from cellprofiler_core.setting import Binary, ValidationError
from cellprofiler_core.setting.choice import Choice
from cellprofiler_core.setting.do_something import DoSomething
from cellprofiler_core.setting.subscriber import ImageSubscriber
from cellprofiler_core.setting.text import Integer, ImageName, Directory, Filename, Float

CUDA_LINK = "https://pytorch.org/get-started/locally/"
Cellpose_link = " https://doi.org/10.1038/s41592-020-01018-x"
LOGGER = logging.getLogger(__name__)


__doc__ = f"""\
RunCellpose
===========

**RunCellpose** uses a pre-trained machine learning model (Cellpose) to detect cells or nuclei in an image.

This module is useful for automating simple segmentation tasks in CellProfiler.
The module accepts greyscale input images and produces an object set. Probabilities can also be captured as an image.

Loading in a model will take slightly longer the first time you run it each session. When evaluating
performance you may want to consider the time taken to predict subsequent images.

The module has been updated to be compatible with the latest release of Cellpose. From the old version of the module the 'cells' model corresponds to 'cyto2' model.

Installation:

It is necessary that you have installed Cellpose version >= 3.0.5

You'll want to run `pip install cellpose` on your CellProfiler Python environment to setup Cellpose. If you have an older version of Cellpose
run 'python -m pip install cellpose --upgrade'.

On the first time loading into CellProfiler, Cellpose will need to download some model files from the internet. This
may take some time. If you want to use a GPU to run the model, you'll need a compatible version of PyTorch and a
supported GPU. Instructions are available at this link: {CUDA_LINK}

Stringer, C., Wang, T., Michaelos, M. et al. Cellpose: a generalist algorithm for cellular segmentation. Nat Methods 18, 100–106 (2021). {Cellpose_link}
============ ============ ===============
Supports 2D? Supports 3D? Respects masks?
============ ============ ===============
YES          YES          NO
============ ============ ===============

"""
MODEL_NAMES = [
    "cyto3", "nuclei", "cyto2_cp3", "tissuenet_cp3", "livecell_cp3", "yeast_PhC_cp3",
    "yeast_BF_cp3", "bact_phase_cp3", "bact_fluor_cp3", "deepbacs_cp3", "cyto2", "cyto"
]

DENOISER_NAMES = ['denoise_cyto3', 'deblur_cyto3', 'upsample_cyto3',
                  'denoise_nuclei', 'deblur_nuclei', 'upsample_nuclei']


# Only these models support size scaling
SIZED_MODELS = {"cyto3", "cyto2", "cyto", "nuclei"}


class RunCellpose(ImageSegmentation):
    category = "Object Processing"

    module_name = "RunCellpose"

    # We use an artificially high revision number to denote our "fork"
    variable_revision_number = 10

    doi = {
        "Please cite the following when using RunCellPose:": "https://doi.org/10.1038/s41592-020-01018-x",
    }

    def __init__(self, **kwargs):
        super(RunCellpose, self).__init__()
        self.current_model = None
        self.current_model_params = None
        self.recon_model = None
        self.recon_model_params = None

    def create_settings(self):
        super(RunCellpose, self).create_settings()

        self.expected_diameter = Integer(
            text="Expected object diameter",
            value=30,
            minval=0,
            doc="""\
The average diameter of the objects to be detected. Setting this to 0 will attempt to automatically detect object size.
Note that automatic diameter mode does not work when running on 3D images or with some of the specialised models.

Cellpose models come with a pre-defined object diameter. Your image will be resized during detection to attempt to
match the diameter expected by the model. The default models have an expected diameter of ~16 pixels, if trying to
detect much smaller objects it may be more efficient to resize the image first using the Resize module.
""",
        )

        self.mode = Choice(
            text="Detection mode",
            choices=MODEL_NAMES,
            value=MODEL_NAMES[0],
            doc="""\
CellPose comes with models for detecting nuclei, cells and other objects. Alternatively, you can supply a custom-trained model
generated using the command line or Cellpose GUI. Custom models can be useful if working with unusual cell types.

The "cyto3" or "nuclei" models are recommended as starting points.
""",
        )

        self.do_3D = Binary(
            text="Use 3D",
            value=False,
            doc="""\
If enabled, 3D specific settings will be available.""",
        )

        self.use_gpu = Binary(
            text="Use GPU",
            value=False,
            doc=f"""\
If enabled, Cellpose will attempt to run detection on your system's graphics card (GPU).
Note that you will need a CUDA-compatible GPU and correctly configured PyTorch version, see this link for details:
{CUDA_LINK}

If disabled or incorrectly configured, Cellpose will run on your CPU instead. This is much slower but more compatible
with different hardware setups.

Note that, particularly when in 3D mode, lack of GPU memory can become a limitation. If a model crashes you may need to
re-start CellProfiler to release GPU memory. Resizing large images prior to running them through the model can free up
GPU memory.
""",
        )

        self.invert = Binary(
            text="Invert images",
            value=False,
            doc="""\
If enabled the image will be inverted and also normalized. For use with fluorescence images using bact model (bact model was trained on phase images""",
        )

        self.supply_nuclei = Binary(
            text="Supply nuclei image as well?",
            value=False,
            doc="""
When detecting whole cells, you can provide a second image featuring a nuclear stain to assist
the model with segmentation. This can help to split touching cells.""",
        )

        self.nuclei_image = ImageSubscriber(
            "Select the nuclei image",
            doc="Select the image you want to use as the nuclear stain.",
        )

        self.save_probabilities = Binary(
            text="Save probability image?",
            value=False,
            doc="""
If enabled, the probability scores from the model will be recorded as a new image.
Probability >0 is considered as being part of a cell.
You may want to use a higher threshold to manually generate objects.""",
        )

        self.probabilities_name = ImageName(
            "Name the probability image",
            "Probabilities",
            doc="Enter the name you want to call the probability image produced by this module.",
        )

        self.model_directory = Directory(
            "Location of the pre-trained model file",
            doc=f"""\
*(Used only when using a custom pre-trained model)*
Select the location of the pre-trained CellPose model file that will be used for detection.""",
        )

        def get_directory_fn():
            """Get the directory for the rules file name"""
            return self.model_directory.get_absolute_path()

        def set_directory_fn(path):
            dir_choice, custom_path = self.model_directory.get_parts_from_path(
                path)

            self.model_directory.join_parts(dir_choice, custom_path)

        self.model_file_name = Filename(
            "Pre-trained model file name",
            "cyto_0",
            get_directory_fn=get_directory_fn,
            set_directory_fn=set_directory_fn,
            doc=f"""\
*(Used only when using a custom pre-trained model)*
This file can be generated by training a custom model withing the CellPose GUI or command line applications.""",
        )

        self.gpu_test = DoSomething(
            "",
            "Test GPU",
            self.do_check_gpu,
            doc=f"""\
Press this button to check whether a GPU is correctly configured.

If you have a dedicated GPU, a failed test usually means that either your GPU does not support deep learning or the
required dependencies are not installed.
If you have multiple GPUs on your system, this button will only test the first one.
""",
        )

        self.flow_threshold = Float(
            text="Flow threshold",
            value=0.4,
            minval=0,
            doc="""\
The flow_threshold parameter is the maximum allowed error of the flows for each mask. The default is flow_threshold=0.4.
Increase this threshold if cellpose is not returning as many masks as you’d expect.
Similarly, decrease this threshold if cellpose is returning too many ill-shaped masks
""",
        )

        self.cellprob_threshold = Float(
            text="Cell probability threshold",
            value=0.0,
            minval=-6.0,
            maxval=6.0,
            doc=f"""\
Cell probability threshold (all pixels with probability above threshold kept for masks). Recommended default is 0.0.
Values vary from -6 to 6
""",
        )

        self.manual_GPU_memory_share = Float(
            text="GPU memory share for each worker",
            value=0.1,
            minval=0.0000001,
            maxval=1,
            doc="""\
Fraction of the GPU memory share available to each worker. Value should be set such that this number times the number
of workers in each copy of CellProfiler times the number of copies of CellProfiler running (if applicable) is <1
""",
        )

        self.stitch_threshold = Float(
            text="Stitch Threshold",
            value=0.0,
            minval=0,
            doc=f"""\
There may be additional differences in YZ and XZ slices that make them unable to be used for 3D segmentation.
In those instances, you may want to turn off 3D segmentation (do_3D=False) and run instead with stitch_threshold>0.
Cellpose will create masks in 2D on each XY slice and then stitch them across slices if the IoU between the mask on the current slice and the next slice is greater than or equal to the stitch_threshold.
""",
        )

        self.min_size = Integer(
            text="Minimum size",
            value=15,
            minval=-1,
            doc="""\
Minimum number of pixels per mask, can turn off by setting value to -1
""",
        )

        self.remove_edge_masks = Binary(
            text="Remove objects that are touching the edge?",
            value=True,
            doc="""
If you do not want to include any object masks that are not in full view in the image, you can have the masks that have pixels touching the the edges removed.
The default is set to "Yes".
""",
        )

        self.denoise = Binary(
            text="Preprocess image before segmentation?",
            value=False,
            doc="""
            If enabled, a separate Cellpose model will be used to clean the input image before segmentation.
            Try this if your input images are blurred, noisy or otherwise need cleanup.
        """,
        )

        self.denoise_type = Choice(
            text="Preprocessing model",
            choices=DENOISER_NAMES,
            value=DENOISER_NAMES[0],
            doc="""\
            Model to use for preprocessing of images. An AI model can be applied to denoise, remove blur or upsample images prior to 
            segmentation. Select nucleus models for nuclei or cyto3 models for anything else.
            
            'Denoise' models may help if your staining is inconsistent.
            'Deblur' attempts to improve out-of-focus images
            'Upsample' will attempt to resize the images so that the object sizes match the native diameter of the segmentation model.
            
            N.b. for upsampling it is essential that the "Expected diameter" setting is correct for the input images
            """,
        )

    def settings(self):
        return [
            self.x_name,
            self.expected_diameter,
            self.mode,
            self.y_name,
            self.use_gpu,
            self.supply_nuclei,
            self.nuclei_image,
            self.save_probabilities,
            self.probabilities_name,
            self.model_directory,
            self.model_file_name,
            self.flow_threshold,
            self.cellprob_threshold,
            self.manual_GPU_memory_share,
            self.stitch_threshold,
            self.do_3D,
            self.min_size,
            self.invert,
            self.remove_edge_masks,
            self.denoise,
            self.denoise_type,
        ]

    def visible_settings(self):

        vis_settings = [self.mode, self.x_name]

        if self.mode.value != "nuclei":
            vis_settings += [self.supply_nuclei]
            if self.supply_nuclei.value:
                vis_settings += [self.nuclei_image]
        if self.mode.value == "custom":
            vis_settings += [
                self.model_directory,
                self.model_file_name,
            ]

        vis_settings += [self.expected_diameter, self.denoise]

        if self.denoise.value:
            vis_settings += [self.denoise_type]

        vis_settings += [
            self.cellprob_threshold,
            self.min_size,
            self.flow_threshold,
            self.y_name,
            self.invert,
            self.save_probabilities
        ]
        if self.save_probabilities.value:
            vis_settings += [self.probabilities_name]
        vis_settings += [self.do_3D]
        if not self.do_3D.value:
            vis_settings += [self.stitch_threshold]

        vis_settings += [self.remove_edge_masks]

        # Our binary never has GPU support
        # vis_settings += [self.remove_edge_masks, self.use_gpu]
        # if self.use_gpu.value:
        #     vis_settings += [self.gpu_test, self.manual_GPU_memory_share]

        return vis_settings

    def validate_module(self, pipeline):
        """If using custom model, validate the model file opens and works"""
        if self.mode.value == "custom":
            model_file = self.model_file_name.value
            model_directory = self.model_directory.get_absolute_path()
            model_path = os.path.join(model_directory, model_file)
            if not os.path.exists(model_path):
                raise ValidationError(f"Failed to open model: {model_path}")

    def load_models(self):
        # Only load new model instances if settings have changed
        from cellpose import models
        if self.use_gpu.value:
            from torch import cuda
            cuda.set_per_process_memory_fraction(
                self.manual_GPU_memory_share.value)

        model_name = self.mode.value
        if model_name == 'custom':
            model_file = self.model_file_name.value
            model_directory = self.model_directory.get_absolute_path()
            model_name = os.path.join(model_directory, model_file)
        model_params = (model_name, self.use_gpu.value)
        if self.current_model_params != model_params:
            LOGGER.info(f"Loading new model: {model_name}")
            if model_name in SIZED_MODELS:
                self.current_model = models.Cellpose(
                    model_type=model_name, gpu=self.use_gpu.value)
            else:
                self.current_model = models.CellposeModel(
                    model_type=model_name, gpu=self.use_gpu.value)
            self.current_model_params = model_params

        if self.denoise.value:
            from cellpose import denoise
            recon_params = (
                self.denoise_type.value,
                self.use_gpu.value,
                self.mode.value != "nuclei" and self.supply_nuclei.value
            )
            if self.recon_model_params != recon_params:
                LOGGER.info(f"Loading new denoiser: {recon_params[0]}")
                self.recon_model = denoise.DenoiseModel(
                    model_type=recon_params[0],
                    gpu=recon_params[1],
                    chan2=recon_params[2]
                )
            self.recon_model_params = recon_params
        else:
            self.recon_model = None
            self.recon_model_params = None

    def run(self, workspace):
        x_name = self.x_name.value
        y_name = self.y_name.value
        images = workspace.image_set
        x = images.get_image(x_name)
        dimensions = x.dimensions
        x_data = x.pixel_data
        anisotropy = 0.0
        if self.do_3D.value:
            anisotropy = x.spacing[0] / x.spacing[1]

        diam = self.expected_diameter.value if self.expected_diameter.value > 0 else None

        if x.multichannel:
            raise ValueError(
                "Color images are not currently supported. Please provide greyscale images."
            )

        if self.mode.value != "nuclei" and self.supply_nuclei.value:
            nuc_image = images.get_image(self.nuclei_image.value)
            # CellPose expects RGB, we'll have a blank red channel, cells in green and nuclei in blue.
            if self.do_3D.value:
                x_data = numpy.stack(
                    (numpy.zeros_like(x_data), x_data, nuc_image.pixel_data),
                    axis=1
                )

            else:
                x_data = numpy.stack(
                    (numpy.zeros_like(x_data), x_data, nuc_image.pixel_data),
                    axis=-1
                )

            channels = [2, 3]
        else:
            channels = [0, 0]

        self.load_models()

        try:
            if self.recon_model is not None:
                input_data = self.recon_model.eval(
                    x_data,
                    diameter=diam,
                    channels=channels
                )
                # Upsampling models scale object diameter to a target size
                if self.denoise_type.value == "upsample_cyto3":
                    diam = 30
                elif self.denoise_type.value == "upsample_nuclei":
                    diam = 17
                # Result only includes input channels
                if self.mode.value != "nuclei" and self.supply_nuclei.value:
                    channels = [0, 1]
            else:
                input_data = x_data

            y_data, flows, *_ = self.current_model.eval(
                input_data,
                channels=channels,
                diameter=diam,
                do_3D=self.do_3D.value,
                anisotropy=anisotropy,
                flow_threshold=self.flow_threshold.value,
                cellprob_threshold=self.cellprob_threshold.value,
                stitch_threshold=self.stitch_threshold.value,
                min_size=self.min_size.value,
                invert=self.invert.value,
            )

            if self.denoise.value and "upsample" in self.denoise_type.value:
                y_data = skimage.transform.resize(y_data, x.pixel_data.shape,
                                                  preserve_range=True, order=0)

            if self.remove_edge_masks:
                from cellpose.utils import remove_edge_masks
                y_data = remove_edge_masks(y_data)

        finally:
            if self.use_gpu.value:
                # Try to clear some GPU memory for other worker processes.
                try:
                    from torch import cuda
                    cuda.empty_cache()
                except Exception as e:
                    print(
                        f"Unable to clear GPU memory. You may need to restart CellProfiler to change models. {e}")

        y = Objects()
        y.segmented = y_data
        y.parent_image = x.parent_image
        objects = workspace.object_set
        objects.add_objects(y, y_name)

        if self.denoise.value and self.show_window:
            # Need to remove unnecessary extra axes
            denoised_image = numpy.squeeze(input_data)
            if "upsample" in self.denoise_type.value:
                denoised_image = skimage.transform.resize(
                    denoised_image, x_data.shape)
            workspace.display_data.recon = denoised_image

        if self.save_probabilities.value:
            # Flows come out sized relative to CellPose's inbuilt model size.
            # We need to slightly resize to match the original image.
            size_corrected = skimage.transform.resize(flows[2], y_data.shape)
            prob_image = Image(
                size_corrected,
                parent_image=x.parent_image,
                convert=False,
                dimensions=len(size_corrected.shape),
            )

            workspace.image_set.add(self.probabilities_name.value, prob_image)

            if self.show_window:
                workspace.display_data.probabilities = size_corrected

        self.add_measurements(workspace)

        if self.show_window:
            if x.volumetric:
                # Can't show CellPose-accepted colour images in 3D
                workspace.display_data.x_data = x.pixel_data
            else:
                workspace.display_data.x_data = x_data
            workspace.display_data.y_data = y_data
            workspace.display_data.dimensions = dimensions

    def display(self, workspace, figure):
        if self.save_probabilities.value or self.denoise.value:
            layout = (2, 2)
        else:
            layout = (2, 1)

        # Fill out the plots as needed
        positions = [(1, 1), (0, 1), (1, 0), (0, 0)]

        figure.set_subplots(
            dimensions=workspace.display_data.dimensions, subplots=layout
        )

        x, y = positions.pop()
        figure.subplot_imshow(
            colormap="gray",
            image=workspace.display_data.x_data,
            title="Input Image",
            x=x,
            y=y,
        )

        if self.denoise.value:
            x, y = positions.pop()
            figure.subplot_imshow(
                colormap="gray",
                image=workspace.display_data.recon,
                sharexy=figure.subplot(0, 0),
                title="Reconstructed image",
                x=x,
                y=y,
            )

        if self.save_probabilities.value:
            x, y = positions.pop()
            figure.subplot_imshow(
                colormap="gray",
                image=workspace.display_data.probabilities,
                sharexy=figure.subplot(0, 0),
                title=self.probabilities_name.value,
                x=x,
                y=y,
            )

        x, y = positions.pop()
        figure.subplot_imshow_labels(
            image=workspace.display_data.y_data,
            sharexy=figure.subplot(0, 0),
            title=self.y_name.value,
            x=x,
            y=y,
        )

    def do_check_gpu(self):
        from cellpose import core
        GPU_works = core.use_gpu()
        if GPU_works:
            message = "GPU appears to be working correctly!"
        else:
            message = (
                "GPU test failed. There may be something wrong with your configuration."
            )
        import wx
        wx.MessageBox(message, caption="GPU Test")

    def upgrade_settings(self, setting_values, variable_revision_number,
                         module_name):
        if variable_revision_number == 10:
            return setting_values, variable_revision_number
        elif variable_revision_number > 4:
            raise ValueError(
                "Module comes from a newer version of the "
                "Broad CellPose plugin. Please use the Glencoe version.")
        if variable_revision_number == 1:
            setting_values = setting_values + ["0.4", "0.0"]
            variable_revision_number = 2
        if variable_revision_number == 2:
            setting_values = setting_values + ["0.0", False, "15", "1.0",
                                               False, False]
            variable_revision_number = 3
        if variable_revision_number == 3:
            setting_values = ([setting_values[0]] + [
                "Python", "CELLPOSE_DOCKER_IMAGE_WITH_PRETRAINED"] +
                              setting_values[1:])
            variable_revision_number = 4
        if variable_revision_number == 4:
            # Remove bad arguments
            for index in (20, 7, 2, 1):
                del setting_values[index]
            setting_values += [False, DENOISER_NAMES[0]]
            setting_values[4] = False
            variable_revision_number = 10

        return setting_values, variable_revision_number
