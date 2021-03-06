import torch
import torch.nn as nn

import os
import sys
import math
from collections import OrderedDict

MY_DIRNAME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(MY_DIRNAME, '..'))

from backbone import backbone_fn


class ProposalModel(nn.Module):

    def __init__(self, config, is_training=True):
        """

        :param config:
        :param is_training:
        """
        super(ProposalModel, self).__init__()

        self.config = config
        self.is_training = is_training
        self.model_params = config["model_params"]

        # backbone
        _backbone_fn = backbone_fn[self.model_params["backbone_name"]]
        self.backbone = _backbone_fn(self.model_params["backbone_pretrained"])
        _out_filters = self.backbone.layers_out_filters

        # embeddind 0
        final_out_filter0 = len(self.config["yolo"]["anchors"][0]) * 5
        self.embedding0 = self._make_embedding([512, 1024], _out_filters[-1], final_out_filter0)  # 1024, 15

        # embedding1
        final_out_filter1 = len(config["yolo"]["anchors"][1]) * 5
        self.embedding1_cbl = self._make_cbl(512, 256, 1)
        self.embedding1_upsample = nn.Upsample(scale_factor=2, mode='nearest')
        self.embedding1 = self._make_embedding([256, 512], _out_filters[-2] + 256, final_out_filter1)  # 768, 15

        # embedding2
        final_out_filter2 = len(config["yolo"]["anchors"][2]) * 5
        self.embedding2_cbl = self._make_cbl(256, 128, 1)
        self.embedding2_upsample = nn.Upsample(scale_factor=2, mode='nearest')
        self.embedding2 = self._make_embedding([128, 256], _out_filters[-3] + 128, final_out_filter2)  # 384, 15


    def _make_cbl(self, _in, _out, ks):
        """
        cbl = conv + batch_norm + leaky_relu, combine convolution, batch-normalization and leaky relu
        :param _in:
        :param _out:
        :param ks:
        :return:
        """
        pad = (ks - 1) // 2 if ks else 0

        return nn.Sequential(
            OrderedDict([
                ("conv", nn.Conv2d(_in, _out, kernel_size=ks, stride=1, padding=pad, bias=False)),
                ("bn", nn.BatchNorm2d(_out)),
                ("relu", nn.LeakyReLU(0.1)),
            ])
        )

    def _make_embedding(self, filters_list, in_filters, out_filter):
        """

        :param filters_list:
        :param in_filters:
        :param out_filter:
        :return:
        """
        m = nn.ModuleList([
            self._make_cbl(in_filters, filters_list[0], 1),  # reformat the channel
            self._make_cbl(filters_list[0], filters_list[1], 3),
            self._make_cbl(filters_list[1], filters_list[0], 1),
            self._make_cbl(filters_list[0], filters_list[1], 3),
            self._make_cbl(filters_list[1], filters_list[0], 1),
            self._make_cbl(filters_list[0], filters_list[1], 3)])

        m.add_module("conv_out", nn.Conv2d(filters_list[1], out_filter, kernel_size=1, stride=1, padding=0, bias=True))
        return m

    def forward(self, x):
        """

        :param x:
        :return:
        """
        def _branch(_embedding, _in):
            for i, e in enumerate(_embedding):
                _in = e(_in)
                if i == 4:  # 4
                    out_branch = _in
            return _in, out_branch

        #  backbone
        x2, x1, x0 = self.backbone(x)

        #  yolo branch 0
        out0, out0_branch = _branch(self.embedding0, x0)  # -1 x 13 x 13 x 15, -1 x 13 x 13 x 512

        #  yolo branch 1
        x1_in = self.embedding1_cbl(out0_branch)  # -1 x 13 x 13 x 256
        x1_in = self.embedding1_upsample(x1_in)  # -1 x 26 x 26 x 256
        x1_in = torch.cat([x1_in, x1], 1)  # -1 x 26 x 26 x 768
        out1, out1_branch = _branch(self.embedding1, x1_in)  # -1 x 26 x 26 x 15, -1 x 26 x 26 x 256

        #  yolo branch 2
        x2_in = self.embedding2_cbl(out1_branch)  # -1 x 26 x 26 x 128
        x2_in = self.embedding2_upsample(x2_in)  # -1 x 52 x 52 x 128
        x2_in = torch.cat([x2_in, x2], 1)  # -1 x 52 x 52 x 384
        out2, out2_branch = _branch(self.embedding2, x2_in)  # -1 x 52 x 52 x 15

        return out0, out1, out2

    def targeted_layer(self):
        """

        :return:
        """
        return self._modules.get('backbone')


if __name__ == "__main__":

    # os.environ["CUDA_VISIBLE_DEVICES"] = '2'

    config = {
        "model_params": {
            "backbone_name": "darknet_53",
            "backbone_pretrained": "",  # set empty to disable
        },
        "yolo": {
            "anchors": [[[116, 90], [156, 198], [373, 326]],
                        [[30, 61], [62, 45], [59, 119]],
                        [[10, 13], [16, 30], [33, 23]]],
        },
    }

    md = ProposalModel(config, is_training=False)
    x = torch.randn(1, 3, 416, 416)

    layer = md.targeted_layer()
    features = list()

    def hook_feature(module, input, output):
        features.append(output[2].data.cpu())
        features.append(output[1].data.cpu())
        features.append(output[0].data.cpu())
    layer.register_forward_hook(hook_feature)

    y0, y1, y2 = md(x)

    print(y0.size())
    print(y1.size())
    print(y2.size())

    print features[0].size()
    print features[1].size()
    print features[2].size()

