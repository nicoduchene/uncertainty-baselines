import os
import pdb
import pickle
import time

import haiku as hk
import jax
import optax
from absl import app, flags
from jax import jit
from jax import random
from tensorflow_probability.substrates import jax as tfp

abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
path = dname + "/../.."
# print(f'Setting working directory to {path}\n')
os.chdir(path)

from baselines.diabetic_retinopathy_detection.fsvi_utils import datasets
from baselines.diabetic_retinopathy_detection.fsvi_utils.utils import (
    get_minibatch,
    initialize_random_keys,
)
from baselines.diabetic_retinopathy_detection.fsvi_utils.utils_training import Training


tfd = tfp.distributions


# original flags
flags.DEFINE_string(
    "data_training",
    "not_specified",
    "Training and in-distribution dataset used (default: not_specified)\n"
    "Examples: 'continual_learning_pmnist', 'continual_learning_smnist', "
    "'continual_learning_sfashionmnist'",
)


flags.DEFINE_string(
    "model_type",
    "not_specified",
    "Model used (default: not_specified). Example: 'fsvi_mlp', 'mfvi_cnn'",
)

flags.DEFINE_string("optimizer", "adam", "Optimizer used (default: adam)")

flags.DEFINE_string(
    "architecture", "not_specified", "Architecture of NN (default: not_specified)",
)

flags.DEFINE_string(
    "activation",
    "not_specified",
    "Activation function used in NN (default: not_specified)",
)

flags.DEFINE_string("prior_mean", "0", "Prior mean function (default: 0)")

flags.DEFINE_string("prior_cov", "0", help="Prior cov function (default: 0)")

flags.DEFINE_string(
    "prior_type",
    default="not_specified",
    help="Type of prior (default: not_specified)",
)

flags.DEFINE_integer(
    "epochs", default=100, help="Number of epochs for each task (default: 100)",
)

flags.DEFINE_integer(
    "batch_size",
    default=100,
    help="Per-core batch size to use for training (default: 100)",
)

flags.DEFINE_float(
    "learning_rate", default=1e-3, help="Learning rate (default: 1e-3)",
)

flags.DEFINE_float("dropout_rate", default=0.0, help="Dropout rate (default: 0.0)")

flags.DEFINE_float(
    "regularization", default=0, help="Regularization parameter (default: 0)",
)

flags.DEFINE_integer(
    "n_inducing_inputs", default=0, help="Number of BNN inducing points (default: 0)",
)

flags.DEFINE_string(
    "inducing_input_type",
    default="not_specified",
    help="Inducing input selection method (default: not_specified)",
)

flags.DEFINE_string("kl_scale", default="1", help="KL scaling factor (default: 1)")

flags.DEFINE_boolean("full_cov", default=False, help="Use full covariance")

flags.DEFINE_integer(
    "n_samples", default=1, help="Number of exp log lik samples (default: 1)",
)

flags.DEFINE_float("tau", default=1.0, help="Likelihood precision (default: 1)")

flags.DEFINE_float("noise_std", default=1.0, help="Likelihood variance (default: 1)")

flags.DEFINE_list(
    "inducing_inputs_bound", default="-1.,1.", help="Inducing point range (default: [-1, 1])"
)

flags.DEFINE_integer(
    "logging_frequency",
    default=10,
    help="Logging frequency in number of epochs (default: 10)",
)

flags.DEFINE_list(
    "figsize", default="10,4", help="Size of figures (default: (10, 4))",
)

flags.DEFINE_integer("seed", default=0, help="Random seed (default: 0)")

flags.DEFINE_string(
    "save_path", default="debug", help="Path to save results (default: debug)",
)

flags.DEFINE_bool("save", default=False, help="Save output to file")

flags.DEFINE_bool("resume_training", default=False, help="Resume training")

flags.DEFINE_bool("map_initialization", default=False, help="MAP initialization")

flags.DEFINE_bool(
    "stochastic_linearization", default=False, help="Stochastic linearization"
)

flags.DEFINE_bool("batch_normalization", default=False, help="Batch normalization")

flags.DEFINE_bool("linear_model", default=False, help="Linear model")

flags.DEFINE_bool("features_fixed", default=False, help="Fixed feature maps")

flags.DEFINE_bool("debug", default=False, help="Debug model")

flags.DEFINE_string(
    "logroot",
    default=None,
    help="The root result folder that store runs for this type of experiment",
)

flags.DEFINE_string(
    "subdir",
    default=None,
    help="The subdirectory in logroot/runs/ corresponding to this run",
)

# new flags copied from deterministic.py
flags.DEFINE_string(
    "output_dir",
    "/tmp/diabetic_retinopathy_detection/deterministic",
    "The directory where the model weights and training/evaluation summaries "
    "are stored. If you aim to use these as trained models for ensemble.py, "
    "you should specify an output_dir name that includes the random seed to "
    "avoid overwriting.",
)
flags.DEFINE_string("data_dir", None, "Path to training and testing data.")

# TODO: decide if we keep this
flags.DEFINE_bool("use_validation", True, "Whether to use a validation split.")


# General model flags.
# TODO: decide if we keep this
flags.DEFINE_integer(
    "checkpoint_interval",
    25,
    "Number of epochs between saving checkpoints. " "Use -1 to never save checkpoints.",
)

# Metric flags.
flags.DEFINE_integer("num_bins", 15, "Number of bins for ECE.")

# Accelerator flags.
flags.DEFINE_bool("force_use_cpu", False, "If True, force usage of CPU")
flags.DEFINE_bool("use_gpu", True, "Whether to run on GPU or otherwise TPU.")
flags.DEFINE_bool("use_bfloat16", False, "Whether to use mixed precision.")
flags.DEFINE_integer("num_cores", 8, "Number of TPU cores or number of GPUs.")
flags.DEFINE_string(
    "tpu",
    None,
    "Name of the TPU. Only used if force_use_cpu and use_gpu are both False.",
)
FLAGS = flags.FLAGS


def process_args():
    """
    This is the only place where it is allowed to modify kwargs

    This function should not have side-effect.

    @param flags: input arguments
    @return:
    """
    # FLAGS doesn't accept renaming!
    FLAGS.figsize = (int(v) for v in FLAGS.figsize)
    FLAGS.inducing_inputs_bound = [float(v) for v in FLAGS.inducing_inputs_bound]


def get_dict_of_flags():
    return {k: getattr(FLAGS, k) for k in dir(FLAGS)}


def main(argv):
    del argv
    process_args()
    kh = initialize_random_keys(seed=FLAGS.seed)
    rng_key, rng_key_train, rng_key_test = random.split(kh.next_key(), 3)

    # LOAD DATA
    (trainloader, input_shape, output_dim, n_train,) = datasets.load_data(
        batch_size=FLAGS.batch_size, data_dir=FLAGS.data_dir
    )

    # INITIALIZE TRAINING CLASS
    training = Training(
        input_shape=input_shape,
        output_dim=output_dim,
        n_train=n_train,
        n_batches=n_train // FLAGS.batch_size,
        # TODO: unify the run.py for classification ood
        full_ntk=False,
        **get_dict_of_flags(),
    )

    # INITIALIZE MODEL
    (model, init_fn, apply_fn, state, params) = training.initialize_model(
        rng_key=rng_key
    )

    # INITIALIZE OPTIMIZATION
    (
        opt,
        opt_state,
        get_trainable_params,
        get_variational_and_model_params,
        metrics,
        loss,
        kl_evaluation,
        log_likelihood_evaluation,
        nll_grad_evaluation,
        task_evaluation,
        prediction_type,
    ) = training.initialize_optimization(
        model=model,
        apply_fn=apply_fn,
        params_init=params,
        state=state,
        rng_key=rng_key,
    )

    # INITIALIZE KL INPUT FUNCTIONS
    inducing_input_fn, prior_fn = training.kl_input_functions(
        apply_fn=apply_fn,
        predict_f_deterministic=model.predict_f_deterministic,
        state=state,
        params=params,
        prior_mean=FLAGS.prior_mean,
        prior_cov=FLAGS.prior_cov,
        rng_key=rng_key,
    )

    # INITIALIZE LOGGING CLASS
    epoch_start = 0
    # logging = utils_logging.Logging(
    #     model=model,
    #     metrics=metrics,
    #     loss=loss,
    #     kl_evaluation=kl_evaluation,
    #     log_likelihood_evaluation=log_likelihood_evaluation,
    #     nll_grad_evaluation=nll_grad_evaluation,
    #     task_evaluation=task_evaluation,
    #     epoch_start=epoch_start,
    #     x_train_permuted=x_train_permuted,
    #     y_train_permuted=y_train_permuted,
    #     x_test=x_test,
    #     y_test=y_test,
    #     x_ood=x_ood,
    #     n_train=n_train,
    #     val_frac=val_frac,
    #     epochs=epochs,
    #     save=save,
    #     save_path=save_path,
    #     model_type=model_type,
    #     **kwargs,
    # )

    @jit
    def update(
        params, state, opt_state, x_batch, y_batch, inducing_inputs, rng_key,
    ):
        trainable_params, non_trainable_params = get_trainable_params(params)
        variational_params, model_params = get_variational_and_model_params(params)
        prior_mean, prior_cov = prior_fn(
            inducing_inputs=inducing_inputs, model_params=model_params,
        )

        grads, new_state = jax.grad(loss, argnums=0, has_aux=True)(
            trainable_params,
            non_trainable_params,
            state,
            prior_mean,
            prior_cov,
            x_batch,
            y_batch,
            inducing_inputs,
            rng_key,
        )

        zero_grads = jax.tree_map(lambda x: x * 0.0, non_trainable_params)
        grads = jax.tree_map(lambda x: x * 1.0, grads)
        grads_full = hk.data_structures.merge(grads, zero_grads)
        updates, opt_state = opt.update(grads_full, opt_state)
        new_params = optax.apply_updates(params, updates)
        params = new_params

        return params, opt_state, new_state

    print(f"\n--- Training for {FLAGS.epochs} epochs ---\n")
    for epoch in range(FLAGS.epochs):
        # logging.t0 = time.time()

        for i, data in enumerate(trainloader, 0):
            rng_key_train, _ = random.split(rng_key_train)

            x_batch, y_batch = get_minibatch(
                data, output_dim, input_shape, prediction_type
            )
            inducing_inputs = inducing_input_fn(x_batch, rng_key_train)
            if "mlp" in FLAGS.model_type:
                x_batch = x_batch.reshape(FLAGS.batch_size, -1)
                inducing_inputs = inducing_inputs.reshape(inducing_inputs.shape[0], -1)

            params, opt_state, state = update(
                params,
                state,
                opt_state,
                x_batch,
                y_batch,
                inducing_inputs,
                rng_key_train,
            )

        # logging.T = time.time()
        # logging.training_progress(epoch, params, state, rng_key_test)
        # logging.training_progress_large(epoch, params, state, x_batch, y_batch, prior_mean, prior_cov, inducing_inputs, rng_key_test)

        # logging.log_training_progress(
        #     epoch, params, state, x_batch, y_batch, x_test, y_test, x_ood,
        #     prior_mean, prior_cov, inducing_inputs, rng_key_test
        # )
        #
        # logging.log_training_metrics(
        #     epoch, x_batch, y_batch, x_test, y_test, x_ood, y_ood, inducing_inputs,
        #     params, state, prior_mean, prior_cov, rng_key_test
        # )


if __name__ == "__main__":
    app.run(main)
