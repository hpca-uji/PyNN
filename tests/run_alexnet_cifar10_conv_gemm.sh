#!/bin/bash

#-------------------------
# Configurable parameters
#-------------------------
DATASET_TRAIN_PATH=${DATASET_TRAIN_PATH:-${HOME}/opt/hpca_pydtnn/data/cifar-10-batches-bin}
DATASET_TEST_PATH=${DATASET_TEST_PATH:-${DATASET_TRAIN_PATH}}
NUM_EPOCHS=${NUM_EPOCHS:-30}
ENABLE_CONV_GEMM=${ENABLE_CONV_GEMM:-True}

#------------------
# OpeMP parameters
#------------------
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-4}
export OMP_DISPLAY_ENV=${OMP_DISPLAY_ENV:-True}

case $(hostname) in
jetson6)
  export GOMP_CPU_AFFINITY="2 4 6 1 3 5 7 0"
  ;;
nowherman)
  export GOMP_CPU_AFFINITY="3 5 7 9 11 13 15 17 19 21 23 25 27 29 31 33 35 37 39 1 2 4 6 8 10 12 14 16 18 20 22 24 26 28 30 32 34 36 38 0"
  ;;
lorca)
  export GOMP_CPU_AFFINITY="4 5 6 7 2 3 1 0"
  ;;
*)
  export OMP_PLACES="cores"
  export OMP_PROC_BIND="close"
  ;;
esac

#---------------------------
# Script related parameters
#---------------------------
SCRIPT_PATH="$(
  cd "$(dirname "$0")" >/dev/null 2>&1 || exit 1
  pwd -P
)"
PARENT_SCRIPT_NAME="$(basename "$(ps $PPID | tail -n 1 | awk '{print $6}')")"
if [ -z "${PARENT_SCRIPT_NAME}" ]; then
  SCRIPT_NAME="$(basename "$0")"
else
  SCRIPT_NAME="${PARENT_SCRIPT_NAME}"
fi
FILE_NAME="$(uname -n)_${SCRIPT_NAME%.sh}_$(printf '%02d' "${OMP_NUM_THREADS:-1}")t-$(date +"%Y%m%d%H%M")"
HISTORY_FILE_NAME="${FILE_NAME}.history"
OUTPUT_FILE_NAME="${FILE_NAME}.out"

python3 -Ou "${SCRIPT_PATH}"/benchmarks_CNN.py \
  --model=alexnet_cifar10 \
  --dataset=cifar10 \
  --dataset_train_path="${DATASET_TRAIN_PATH}" \
  --dataset_test_path="${DATASET_TEST_PATH}" \
  --test_as_validation=True \
  --batch_size=64 \
  --validation_split=0.2 \
  --steps_per_epoch=0 \
  --num_epochs=${NUM_EPOCHS} \
  --evaluate=True \
  --optimizer=sgd \
  --learning_rate=0.01 \
  --momentum=0.9 \
  --loss=categorical_cross_entropy \
  --metrics=categorical_accuracy \
  --lr_schedulers=early_stopping,reduce_lr_on_plateau \
  --warm_up_epochs=5 \
  --early_stopping_metric=val_categorical_cross_entropy \
  --early_stopping_patience=10 \
  --reduce_lr_on_plateau_metric=val_categorical_cross_entropy \
  --reduce_lr_on_plateau_factor=0.1 \
  --reduce_lr_on_plateau_patience=5 \
  --reduce_lr_on_plateau_min_lr=0 \
  --parallel=sequential \
  --non_blocking_mpi=False \
  --tracing=False \
  --profile=False \
  --enable_gpu=False \
  --dtype=float32 \
  --enable_conv_gemm="${ENABLE_CONV_GEMM}" \
  --history="${HISTORY_FILE_NAME}" \
  | tee "${OUTPUT_FILE_NAME}"
