#!/usr/bin/env python3

import argparse
import gzip
import os
import numpy as np
import proto.net_pb2 as pb

LC0_MAJOR = 0
LC0_MINOR = 21
LC0_MINOR_WITH_INPUT_TYPE_3 = 25
LC0_MINOR_WITH_INPUT_TYPE_4 = 26
LC0_MINOR_WITH_INPUT_TYPE_5 = 27
LC0_MINOR_WITH_MISH = 29
LC0_MINOR_WITH_ATTN_BODY = 30
LC0_MINOR_WITH_MULTIHEAD = 31
LC0_PATCH = 0
WEIGHTS_MAGIC = 0x1c0


def nested_getattr(obj, attr):
    attributes = attr.split(".")
    for a in attributes:
        try:
            obj = getattr(obj, a)
        except Exception as e:
            print("Error getting attribute {} in {}".format(a, attr))
            raise e
    return obj


class Net:

    def __init__(self,
                 net=pb.NetworkFormat.NETWORK_SE_WITH_HEADFORMAT,
                 input=pb.NetworkFormat.INPUT_CLASSICAL_112_PLANE,
                 value=pb.NetworkFormat.VALUE_CLASSICAL,
                 policy=pb.NetworkFormat.POLICY_CLASSICAL,
                 moves_left=pb.NetworkFormat.MOVES_LEFT_V1):

        if net == pb.NetworkFormat.NETWORK_SE:
            net = pb.NetworkFormat.NETWORK_SE_WITH_HEADFORMAT
        if net == pb.NetworkFormat.NETWORK_CLASSICAL:
            net = pb.NetworkFormat.NETWORK_CLASSICAL_WITH_HEADFORMAT

        self.pb = pb.Net()
        self.pb.magic = WEIGHTS_MAGIC
        self.pb.min_version.major = LC0_MAJOR
        self.pb.min_version.minor = LC0_MINOR
        self.pb.min_version.patch = LC0_PATCH
        self.pb.format.weights_encoding = pb.Format.LINEAR16

        self.weights = []

        self.set_networkformat(net)
        self.pb.format.network_format.input = input
        self.set_policyformat(policy)
        self.set_valueformat(value)
        self.set_movesleftformat(moves_left)
        self.set_defaultactivation(pb.NetworkFormat.DEFAULT_ACTIVATION_RELU)

    def set_networkformat(self, net):
        self.pb.format.network_format.network = net
        if net == pb.NetworkFormat.NETWORK_ATTENTIONBODY_WITH_HEADFORMAT \
                and self.pb.min_version.minor < LC0_MINOR_WITH_ATTN_BODY:
            self.pb.min_version.minor = LC0_MINOR_WITH_ATTN_BODY
        if net == pb.NetworkFormat.NETWORK_ATTENTIONBODY_WITH_MULTIHEADFORMAT \
                and self.pb.min_version.minor < LC0_MINOR_WITH_MULTIHEAD:
            self.pb.min_version.minor = LC0_MINOR_WITH_MULTIHEAD

    def set_policyformat(self, policy):
        self.pb.format.network_format.policy = policy

    def set_headcount(self, headcount):
        self.pb.weights.headcount = headcount

    def set_pol_headcount(self, headcount):
        self.pb.weights.pol_headcount = headcount

    def set_valueformat(self, value):
        self.pb.format.network_format.value = value

        # OutputFormat is for search to know which kind of value the net returns.
        if value == pb.NetworkFormat.VALUE_WDL:
            self.pb.format.network_format.output = pb.NetworkFormat.OUTPUT_WDL
        else:
            self.pb.format.network_format.output = pb.NetworkFormat.OUTPUT_CLASSICAL

    def set_movesleftformat(self, moves_left):
        self.pb.format.network_format.moves_left = moves_left

    def set_input(self, input_format):
        self.pb.format.network_format.input = input_format
        if input_format == pb.NetworkFormat.INPUT_112_WITH_CANONICALIZATION_V2 or input_format == pb.NetworkFormat.INPUT_112_WITH_CANONICALIZATION_V2_ARMAGEDDON:
            self.pb.min_version.minor = LC0_MINOR_WITH_INPUT_TYPE_5
        elif input_format >= pb.NetworkFormat.INPUT_112_WITH_CANONICALIZATION_HECTOPLIES:
            self.pb.min_version.minor = LC0_MINOR_WITH_INPUT_TYPE_4
        # Input type 2 was available before 3, but it was buggy, so also limit it to same version as 3.
        elif input_format != pb.NetworkFormat.INPUT_CLASSICAL_112_PLANE:
            self.pb.min_version.minor = LC0_MINOR_WITH_INPUT_TYPE_3

    def set_defaultactivation(self, activation):
        self.pb.format.network_format.default_activation = activation
        if activation == pb.NetworkFormat.DEFAULT_ACTIVATION_MISH:
            if self.pb.min_version.minor < LC0_MINOR_WITH_MISH:
                self.pb.min_version.minor = LC0_MINOR_WITH_MISH

    def set_smolgen_activation(self, activation):
        self.pb.format.network_format.smolgen_activation = activation
        if self.pb.min_version.minor < LC0_MINOR_WITH_ATTN_BODY:
            self.pb.min_version.minor = LC0_MINOR_WITH_ATTN_BODY
        return None

    def set_ffn_activation(self, activation):
        self.pb.format.network_format.ffn_activation = activation
        if self.pb.min_version.minor < LC0_MINOR_WITH_ATTN_BODY:
            self.pb.min_version.minor = LC0_MINOR_WITH_ATTN_BODY
        return None

    def set_input_embedding(self, embedding):
        self.pb.format.network_format.input_embedding = embedding
        if self.pb.min_version.minor < LC0_MINOR_WITH_MULTIHEAD:
            self.pb.min_version.minor = LC0_MINOR_WITH_MULTIHEAD

    def activation(self, name):
        if name == "relu":
            return pb.NetworkFormat.ACTIVATION_RELU
        elif name == "tanh":
            return pb.NetworkFormat.ACTIVATION_TANH
        elif name == "sigmoid":
            return pb.NetworkFormat.ACTIVATION_SIGMOID
        elif name == "softmax":
            return pb.NetworkFormat.ACTIVATION_SOFTMAX
        elif name == "selu":
            return pb.NetworkFormat.ACTIVATION_SELU
        elif name == "mish":
            return pb.NetworkFormat.ACTIVATION_MISH
        elif name == "swish":
            return pb.NetworkFormat.ACTIVATION_SWISH
        elif name == "relu_2" or name == "sqrrelu":
            return pb.NetworkFormat.ACTIVATION_RELU_2
        elif name == "default":
            return pb.NetworkFormat.ACTIVATION_DEFAULT
        else:
            return pb.NetworkFormat.ACTIVATION_NONE
        
        
    def fill_layer_v2(self, layer, params):
        """Normalize and populate 16bit layer in protobuf"""
        params = params.flatten().astype(np.float32)
        layer.min_val = 0 if len(params) == 1 else float(np.min(params))
        layer.max_val = 1 if len(params) == 1 and np.max(
            params) == 0 else float(np.max(params))
        if layer.max_val == layer.min_val:
            # Avoid division by zero if max == min.
            params = (params - layer.min_val)
        else:
            params = (params - layer.min_val) / (layer.max_val - layer.min_val)
        params *= 0xffff
        params = np.round(params)
        layer.params = params.astype(np.uint16).tobytes()

    def fill_layer(self, layer, weights):
        """Normalize and populate 16bit layer in protobuf"""
        params = np.array(weights.pop(), dtype=np.float32)
        layer.min_val = 0 if len(params) == 1 else float(np.min(params))
        layer.max_val = 1 if len(params) == 1 and np.max(
            params) == 0 else float(np.max(params))
        if layer.max_val == layer.min_val:
            # Avoid division by zero if max == min.
            params = (params - layer.min_val)
        else:
            params = (params - layer.min_val) / (layer.max_val - layer.min_val)
        params *= 0xffff
        params = np.round(params)
        layer.params = params.astype(np.uint16).tobytes()

    def denorm_layer_v2(self, layer):
        """Denormalize a layer from protobuf"""
        params = np.frombuffer(layer.params, np.uint16).astype(np.float32)
        params /= 0xffff
        return params * (layer.max_val - layer.min_val) + layer.min_val

    def denorm_layer(self, layer, weights):
        weights.insert(0, self.denorm_layer_v2(layer))


    def save_proto(self, filename):
        """Save weights gzipped protobuf file"""
        if len(filename.split('.')) == 1:
            filename += ".pb.gz"

        with gzip.open(filename, 'wb') as f:
            data = self.pb.SerializeToString()
            f.write(data)

        size = os.path.getsize(filename) / 1024**2
        print("Weights saved as '{}' {}M".format(filename, round(size, 2)))

    def tf_name_to_pb_name(self, name):
        """Given Tensorflow variable name returns the protobuf name and index
        of residual block if weight belong in a residual block."""
        def value_to_bp(l, w):
            if l == 'dense_error':
                w = w.split(':')[0]
                d = {'kernel': 'ip_val_err_w', 'bias': 'ip_val_err_b'}
                return d[w]
            elif l == 'dense_cat':
                w = w.split(':')[0]
                d = {'kernel': 'ip_val_cat_w', 'bias': 'ip_val_cat_b'}
                return d[w]
            if l == 'embedding':
                n = ''
            elif l == 'dense1':
                n = 1
            elif l == 'dense2':
                n = 2
            else:
                raise ValueError('Unable to decode value weight {}/{}'.format(
                    l, w))
            w = w.split(':')[0]
            d = {'kernel': 'ip{}_val_w', 'bias': 'ip{}_val_b'}

            return d[w].format(n)
        
        def attn_pol_to_bp(l, w):
            if l == 'wq':
                n = 2
            elif l == 'wk':
                n = 3
            elif l == 'ppo':
                n = 4
            else:
                raise ValueError(
                    'Unable to decode attn_policy weight {}/{}'.format(l, w))
            w = w.split(':')[0]
            d = {'kernel': 'ip{}_pol_w', 'bias': 'ip{}_pol_b'}

            return d[w].format(n)

        def encoder_to_bp(l, w):
            w = w.split(':')[0]
            d = {'gamma': '{}_gammas', 'beta': '{}_betas'}

            return d[w].format(l)

        def mha_to_bp(l, w):
            s = ''
            # for these first two the only possibilities are the input step sizes
            if l == 'quantize_1':
                return 's1'
            elif l == 'quantize_2':
                return 's2'
            elif l.startswith('rpe'):
                return l
            elif l.startswith('dense'):
                s = 'dense'
            elif l.startswith('w'):
                s = l[1]
                

            else:
                raise ValueError('Unable to decode mha weight {}/{}'.format(
                    l, w))
            w = w.split(':')[0]
            d = {'kernel': '{}_w', 'bias': '{}_b', 's': '{}_s'}

            return d[w].format(s)

        def mha_smolgen_to_bp(l, w):
            s = {
                'compress': 'compress',
                'hidden1_dense': 'dense1_{}',
                'hidden1_ln': 'ln1_{}',
                'gen_from': 'dense2_{}',
                'gen_from_ln': 'ln2_{}'
            }
            if s[l] is None:
                raise ValueError(
                    'Unable to decode mha smolgen weight {}/{}'.format(l, w))
            w = w.split(':')[0]
            d = {
                'kernel': 'w',
                'bias': 'b',
                'gamma': 'gammas',
                'beta': 'betas'
            }

            return s[l].format(d[w])

        def ffn_to_bp(l, w):
            w = w.split(':')[0]
            if l == 'quantize_1':
                return 's1'
            elif l == 'quantize_2':
                return 's2'
            d = {'kernel': '{}_w', 'bias': '{}_b', 's': '{}_s'}

            return d[w].format(l)

        def moves_left_to_bp(l, w):
            if l == 'embedding':
                n = ''
            elif l == 'dense1':
                n = 1
            elif l == 'dense2':
                n = 2
            else:
                raise ValueError(
                    'Unable to decode moves_left weight {}/{}'.format(l, w))
            w = w.split(':')[0]
            d = {'kernel': 'ip{}_mov_w', 'bias': 'ip{}_mov_b'}

            return d[w].format(n)

        layers = name.split('/')
        base_layer = layers[0]
        weights_name = layers[-1]
        pb_name = None
        block = None
        encoder_block = None
        pol_encoder_block = None

        if base_layer == 'policy':
            pb_prefix = 'policy_heads.'
            if layers[1] == 'embedding':
                if layers[2].split(':')[0] == 'kernel':
                    pb_name = pb_prefix + 'ip_pol_w'
                else:
                    pb_name = pb_prefix + 'ip_pol_b'
                
            elif layers[1] in ['vanilla', 'soft', 'optimistic_st', 'opponent', 'next']:
                pb_prefix = pb_prefix + layers[1] + '.'
                if layers[2] == 'attention':
                    pb_name = attn_pol_to_bp(layers[3], weights_name)
                pb_name = pb_prefix + pb_name

        elif base_layer == 'value':
            pb_prefix = ''
            if layers[1] in ['st', 'q', 'winner']:
                pb_prefix = 'value_heads.' + layers[1] + '.'
            if 'dense' in layers[2] or 'embedding' in layers[2]:
                pb_name = value_to_bp(layers[2], weights_name)
            pb_name = pb_prefix + pb_name

        elif base_layer == 'moves_left':
            if 'dense' in layers[1] or 'embedding' in layers[1]:
                pb_name = moves_left_to_bp(layers[1], weights_name)

        elif base_layer.startswith('encoder'):
            encoder_block = int(base_layer.split('_')[1]) - 1
            if layers[1] == 'mha':
                if layers[2] == 'smolgen':
                    pb_name = 'mha.smolgen.' + mha_smolgen_to_bp(
                        layers[3], weights_name)
                else:
                    pb_name = 'mha.' + mha_to_bp(layers[2], weights_name)
            elif layers[1] == 'ffn':
                pb_name = 'ffn.' + ffn_to_bp(layers[2], weights_name)
            else:
                pb_name = encoder_to_bp(layers[1], weights_name)
                
        elif base_layer == 'embedding':
            if layers[1].split(':')[0] == 'kernel':
                pb_name = 'ip_emb_w'
            elif layers[1].split(':')[0] == 'bias':
                pb_name = 'ip_emb_b'
            elif layers[1] == 'ffn':
                pb_name = 'ip_emb_ffn.' + ffn_to_bp(layers[2], weights_name)
            elif layers[1] in ['ln', 'ffn_ln']:
                pb_name = 'ip_emb_' + encoder_to_bp(layers[1], weights_name)
            elif layers[1] == 'preprocess':
                if layers[2].split(':')[0] == 'kernel':
                    pb_name = 'ip_emb_preproc_w'
                else:
                    pb_name = 'ip_emb_preproc_b'
            if layers[1] == 'mult_gate' or layers[1] == 'add_gate':
                if layers[2].split(':')[0] == 'gate':
                    pb_name = 'ip_{}'.format(layers[1])

        elif base_layer == 'smol_weight_gen':
            if layers[1].split(':')[0] == 'kernel':
                pb_name = 'smolgen_w'
            else:
                pb_name = 'smolgen_b'
        
        else:
            raise ValueError('Unable to decode layer {}'.format(name))

        return (pb_name, block, pol_encoder_block, encoder_block)

    def get_weights_v2(self, names):
        # `names` is a list of Tensorflow tensor names to get from the protobuf.
        # Returns list of [Tensor name, Tensor weights].
        tensors = {}

        for tf_name in names:
            name = tf_name
            if 'stddev' in name:
                # Get variance instead of stddev.
                name = name.replace('stddev', 'variance')
            if 'renorm' in name:
                # Renorm variables are not populated.
                continue
            if 'headcount' in tf_name:
                # headcount is set with set_headcount()
                continue

            pb_name, block, pol_encoder_block, encoder_block = self.tf_name_to_pb_name(
                name)

            if pb_name is None:
                raise ValueError(
                    "Don't know where to store weight in protobuf: {}".format(
                        name))

            if block is None:
                if pol_encoder_block is not None:
                    pb_weights = self.pb.weights.pol_encoder[pol_encoder_block]
                elif encoder_block is not None:
                    pb_weights = self.pb.weights.encoder[encoder_block]
                else:
                    pb_weights = self.pb.weights
            else:
                pb_weights = self.pb.weights.residual[block]

            w = self.denorm_layer_v2(nested_getattr(pb_weights, pb_name))

            # Only variance is stored in the protobuf.
            if 'stddev' in tf_name:
                w = np.sqrt(w + 1e-5)

            tensors[tf_name] = w
        return tensors


    def parse_proto(self, filename):
        with gzip.open(filename, 'rb') as f:
            self.pb = self.pb.FromString(f.read())
        # Populate policyFormat and valueFormat fields in old protobufs
        # without these fields.
        if self.pb.format.network_format.network == pb.NetworkFormat.NETWORK_CLASSICAL:
            self.set_networkformat(
                pb.NetworkFormat.NETWORK_CLASSICAL_WITH_HEADFORMAT)
            self.set_valueformat(pb.NetworkFormat.VALUE_CLASSICAL)
            self.set_policyformat(pb.NetworkFormat.POLICY_CLASSICAL)
            self.set_movesleftformat(pb.NetworkFormat.MOVES_LEFT_NONE)



    def fill_net_v2(self, all_weights):
        # all_weights is array of [name of weight, numpy array of weights].
        self.pb.format.weights_encoding = pb.Format.LINEAR16

        has_renorm = any('renorm' in w[0] for w in all_weights)
        weight_names = [w[0] for w in all_weights]

        del self.pb.weights.residual[:]

        for name, weights in all_weights:
            layers = name.split('/')
            weights_name = layers[-1]
            if weights.ndim == 4:
                # Convolution weights need a transpose
                #
                # TF
                # [filter_height, filter_width, in_channels, out_channels]
                #
                # Leela
                # [output, input, filter_size, filter_size]
                weights = np.transpose(weights, axes=[3, 2, 0, 1])
            elif weights.ndim == 2:
                # Fully connected layers are [in, out] in TF
                #
                # [out, in] in Leela
                #
                weights = np.transpose(weights, axes=[1, 0])

            if 'renorm' in name:
                # Batch renorm has extra weights, but we don't know what to do with them.
                continue
            if has_renorm:
                if 'variance:' in weights_name:
                    # Renorm has variance, but it is not the primary source of truth.
                    continue
                # Renorm has moving stddev not variance, undo the transform to make it compatible.
                if 'stddev:' in weights_name:
                    weights = np.square(weights) - 1e-5
                    name = name.replace('stddev', 'variance')

            if self.pb.format.network_format.input < pb.NetworkFormat.INPUT_112_WITH_CANONICALIZATION_HECTOPLIES:
                if name == 'embedding/kernel:0':
                    weights[:, 109] /= 99

            pb_name, block, pol_encoder_block, encoder_block = self.tf_name_to_pb_name(
                name)

            if pb_name is None:
                raise ValueError(
                    "Don't know where to store weight in protobuf: {}".format(
                        name))

            if block is None:
                if pol_encoder_block is not None:
                    assert pol_encoder_block >= 0
                    while pol_encoder_block >= len(
                            self.pb.weights.pol_encoder):
                        self.pb.weights.pol_encoder.add()
                    pb_weights = self.pb.weights.pol_encoder[pol_encoder_block]
                elif encoder_block is not None:
                    assert encoder_block >= 0
                    while encoder_block >= len(self.pb.weights.encoder):
                        self.pb.weights.encoder.add()
                    pb_weights = self.pb.weights.encoder[encoder_block]
                else:
                    pb_weights = self.pb.weights
            else:
                assert block >= 0
                while block >= len(self.pb.weights.residual):
                    self.pb.weights.residual.add()
                pb_weights = self.pb.weights.residual[block]

            self.fill_layer_v2(nested_getattr(pb_weights, pb_name), weights)

def print_pb_stats(obj, parent=None):
    for descriptor in obj.DESCRIPTOR.fields:
        value = getattr(obj, descriptor.name)
        if descriptor.name == "weights":
            return
        if descriptor.type == descriptor.TYPE_MESSAGE:
            if descriptor.label == descriptor.LABEL_REPEATED:
                map(print_pb_stats, value)
            else:
                print_pb_stats(value, obj)
        elif descriptor.type == descriptor.TYPE_ENUM:
            enum_name = descriptor.enum_type.values[value].name
            print("%s: %s" % (descriptor.full_name, enum_name))
        else:
            print("%s: %s" % (descriptor.full_name, value))


def main(argv):
    net = Net()

    if argv.input.endswith(".txt"):
        raise ValueError("This script is only for protobufs")
        print('Found .txt network')
        net.parse_txt(argv.input)
        net.print_stats()
        if argv.output == None:
            argv.output = argv.input.replace('.txt', '.pb.gz')
            assert argv.output.endswith('.pb.gz')
            print('Writing output to: {}'.format(argv.output))
        net.save_proto(argv.output)
    elif argv.input.endswith(".pb.gz"):
        print('Found .pb.gz network')
        net.parse_proto(argv.input)
        net.print_stats()
        if argv.output == None:
            argv.output = argv.input.replace('.pb.gz', '.txt.gz')
            print('Writing output to: {}'.format(argv.output))
            assert argv.output.endswith('.txt.gz')
        if argv.output.endswith(".pb.gz"):
            net.save_proto(argv.output)
    else:
        print('Unable to detect the network format. '
              'Filename should end in ".txt" or ".pb.gz"')


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(
        description='Convert network textfile to proto.')
    argparser.add_argument('-i',
                           '--input',
                           type=str,
                           help='input network weight text file')
    argparser.add_argument('-o',
                           '--output',
                           type=str,
                           help='output filepath without extension')
    main(argparser.parse_args())