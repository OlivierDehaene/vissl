# Copyright (c) Facebook, Inc. and its affiliates.

# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import os
import unittest

from hydra.experimental import compose, initialize_config_module
from vissl.config.attr_dict import AttrDict
from vissl.utils.hydra_config import convert_to_attrdict
from vissl.utils.test_utils import (
    gpu_test,
    in_temporary_directory,
    run_integration_test,
)


class TestStateCheckpointing(unittest.TestCase):
    """
    Check that loading a checkpoint during training works
    Check that loading a checkpoint for benchmarking works
    """

    @staticmethod
    def _create_pretraining_config(with_fsdp: bool):
        with initialize_config_module(config_module="vissl.config"):
            cfg = compose(
                "defaults",
                overrides=[
                    "config=test/integration_test/quick_swav",
                    "+config/pretrain/swav/models=regnet16Gf",
                    "config.DATA.TRAIN.DATA_SOURCES=[synthetic]",
                    "config.DATA.TRAIN.DATA_LIMIT=40",
                    "config.SEED_VALUE=0",
                    "config.MODEL.AMP_PARAMS.USE_AMP=False",
                    "config.MODEL.SYNC_BN_CONFIG.CONVERT_BN_TO_SYNC_BN=True",
                    "config.MODEL.SYNC_BN_CONFIG.SYNC_BN_TYPE=pytorch",
                    "config.MODEL.AMP_PARAMS.AMP_TYPE=pytorch",
                    "config.LOSS.swav_loss.epsilon=0.03",
                    "config.MODEL.FSDP_CONFIG.flatten_parameters=True",
                    "config.MODEL.FSDP_CONFIG.mixed_precision=False",
                    "config.MODEL.FSDP_CONFIG.fp32_reduce_scatter=False",
                    "config.MODEL.FSDP_CONFIG.compute_dtype=float32",
                    "config.DISTRIBUTED.NUM_PROC_PER_NODE=2",
                    "config.LOG_FREQUENCY=1",
                    "config.OPTIMIZER.construct_single_param_group_only=True",
                    "config.DATA.TRAIN.BATCHSIZE_PER_REPLICA=4",
                    "config.OPTIMIZER.use_larc=False",
                    "config.REPRODUCIBILITY.CUDDN_DETERMINISTIC=True",
                    "config.DATA.TRAIN.USE_DEBUGGING_SAMPLER=True",
                ],
            )
        args, config = convert_to_attrdict(cfg)
        if with_fsdp:
            config["MODEL"]["TRUNK"]["NAME"] = "regnet_fsdp"
            config["MODEL"]["HEAD"]["PARAMS"][0][0] = "swav_head_fsdp"
            config.TRAINER.TASK_NAME = "self_supervision_fsdp_task"
        else:
            config["MODEL"]["TRUNK"]["NAME"] = "regnet_v2"
            config["MODEL"]["HEAD"]["PARAMS"][0][0] = "swav_head"
        return config

    def run_preemption_test(self, config: AttrDict, compare_losses: bool = True):
        initial_result = run_integration_test(config)
        initial_iters, initial_losses = initial_result.get_losses_with_iterations()

        initial_result.clean_final_checkpoint()
        initial_result.clean_logs()

        restart_result = run_integration_test(config)
        restart_iters, restart_losses = restart_result.get_losses_with_iterations()

        print("INITIAL:", initial_iters, initial_losses)
        print("RESTART:", restart_iters, restart_losses)
        self.assertEqual(initial_iters[-len(restart_iters) :], restart_iters)
        if compare_losses:
            self.assertEqual(initial_losses[-len(restart_losses) :], restart_losses)

    @gpu_test(gpu_count=2)
    def test_restart_after_preemption_at_epoch(self):
        with in_temporary_directory():
            config = self._create_pretraining_config(with_fsdp=False)
            config.OPTIMIZER.num_epochs = 2
            self.run_preemption_test(config)

    @gpu_test(gpu_count=2)
    def test_restart_after_preemption_at_epoch_fsdp(self):
        with in_temporary_directory():
            config = self._create_pretraining_config(with_fsdp=True)
            config.OPTIMIZER.num_epochs = 2
            self.run_preemption_test(config)

    @gpu_test(gpu_count=2)
    def test_restart_after_preemption_at_iteration(self):
        with in_temporary_directory():
            config = self._create_pretraining_config(with_fsdp=False)
            config.CHECKPOINT.CHECKPOINT_ITER_FREQUENCY = 3
            # TODO - understand why the losses do not match exactly for iteration preemption
            self.run_preemption_test(config, compare_losses=False)

    @gpu_test(gpu_count=2)
    def test_restart_after_preemption_at_iteration_fsdp(self):
        with in_temporary_directory():
            config = self._create_pretraining_config(with_fsdp=True)
            config.CHECKPOINT.CHECKPOINT_ITER_FREQUENCY = 3
            # TODO - understand why the losses do not match exactly for iteration preemption
            self.run_preemption_test(config, compare_losses=False)

    @staticmethod
    def _create_benchmark_config(checkpoint_path: str, with_fsdp: bool):
        with initialize_config_module(config_module="vissl.config"):
            cfg = compose(
                "defaults",
                overrides=[
                    "config=debugging/benchmark/linear_image_classification/eval_resnet_8gpu_transfer_imagenette_160",
                    "+config/debugging/benchmark/linear_image_classification/models=regnet16Gf_eval_mlp",
                    f"config.MODEL.WEIGHTS_INIT.PARAMS_FILE={checkpoint_path}",
                    "config.SEED_VALUE=2",
                    "config.MODEL.AMP_PARAMS.AMP_TYPE=pytorch",
                    "config.MODEL.SYNC_BN_CONFIG.SYNC_BN_TYPE=pytorch",
                    "config.OPTIMIZER.num_epochs=1",
                    "config.OPTIMIZER.param_schedulers.lr.lengths=[0.1, 0.9]",
                    "config.OPTIMIZER.param_schedulers.lr.name=cosine",
                    "config.LOSS.swav_loss.epsilon=0.03",
                    "config.DATA.TRAIN.DATA_SOURCES=[synthetic]",
                    "config.DATA.TRAIN.LABEL_SOURCES=[synthetic]",
                    "config.DATA.TEST.DATA_SOURCES=[synthetic]",
                    "config.DATA.TEST.LABEL_SOURCES=[synthetic]",
                    "config.DATA.TRAIN.DATA_LIMIT=40",
                    "config.DATA.TEST.DATA_LIMIT=16",
                    "config.DISTRIBUTED.NCCL_DEBUG=False",
                    "config.MODEL.AMP_PARAMS.USE_AMP=false",
                    "config.MODEL.FSDP_CONFIG.mixed_precision=false",
                    "config.OPTIMIZER.use_larc=false",
                    "config.MODEL.SYNC_BN_CONFIG.CONVERT_BN_TO_SYNC_BN=True",  # This is critical
                    "config.REPRODUCIBILITY.CUDDN_DETERMINISTIC=True",
                    "config.DATA.TRAIN.USE_DEBUGGING_SAMPLER=True",
                    "config.DATA.TEST.USE_DEBUGGING_SAMPLER=True",
                    "config.DATA.TRAIN.BATCHSIZE_PER_REPLICA=4",
                    "config.DATA.TEST.BATCHSIZE_PER_REPLICA=4",
                    "config.MODEL.FSDP_CONFIG.flatten_parameters=True",
                    "config.MODEL.FSDP_CONFIG.fp32_reduce_scatter=false",
                    "config.OPTIMIZER.construct_single_param_group_only=True",
                    "config.OPTIMIZER.num_epochs=2",
                    "config.DISTRIBUTED.NUM_NODES=1",
                    "config.DISTRIBUTED.NUM_PROC_PER_NODE=2",
                ],
            )
        args, config = convert_to_attrdict(cfg)
        if with_fsdp:
            config["MODEL"]["TRUNK"]["NAME"] = "regnet_fsdp"
            config["MODEL"]["HEAD"]["PARAMS"][0][0] = "eval_mlp_fsdp"
            config.TRAINER.TASK_NAME = "self_supervision_fsdp_task"
        else:
            config["MODEL"]["TRUNK"]["NAME"] = "regnet_v2"
            config["MODEL"]["HEAD"]["PARAMS"][0][0] = "eval_mlp"
        return config

    def run_benchmarking(self, checkpoint_path: str, with_fsdp: bool):
        with in_temporary_directory():
            config = self._create_benchmark_config(checkpoint_path, with_fsdp=with_fsdp)
            results = run_integration_test(config)
            return results.get_losses(), results.get_accuracies()

    @unittest.skip(
        "FAILING due to https://github.com/facebookresearch/fairscale/issues/643"
    )
    @gpu_test(gpu_count=2)
    def test_benchmarking_from_a_consolidated_checkpoint(self):
        with in_temporary_directory() as checkpoint_folder:
            # Run a pre-training in DDP mode and save a consolidated checkpoint
            config = self._create_pretraining_config(with_fsdp=False)
            run_integration_test(config)
            checkpoint_path = os.path.join(checkpoint_folder, "checkpoint.torch")

            # Now, run both DDP and FSDP linear evaluation and compare the traces
            ddp_losses, ddp_accuracies = self.run_benchmarking(
                checkpoint_path, with_fsdp=False
            )
            fsdp_losses, fsdp_accuracies = self.run_benchmarking(
                checkpoint_path, with_fsdp=True
            )
            self.assertEqual(ddp_losses, fsdp_losses)
            self.assertEqual(ddp_accuracies, fsdp_accuracies)

    @unittest.skip(
        "FAILING due to https://github.com/facebookresearch/fairscale/issues/643"
    )
    @gpu_test(gpu_count=2)
    def test_benchmarking_from_sharded_checkpoint(self):
        with in_temporary_directory() as checkpoint_folder:
            # Run a pre-training in FSDP mode and save a sharded checkpoing
            config = self._create_pretraining_config(with_fsdp=True)
            run_integration_test(config)
            checkpoint_path = os.path.join(checkpoint_folder, "checkpoint.torch")

            # Verify that FSDP can load the checkpoint and run a benchmark on it
            fsdp_losses, fsdp_accuracies = self.run_benchmarking(
                checkpoint_path, with_fsdp=True
            )
            self.assertGreaterEqual(len(fsdp_losses), 0)
            self.assertEqual(4, len(fsdp_accuracies))
