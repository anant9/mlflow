"""
Example of hyperparameter search in MLflow using Hyperopt.

The run method will instantiate and run Hyperopt optimizer. Each parameter configuration is
evaluated in a new MLflow run invoking main entry point with selected parameters.

The runs are evaluated based on validation set loss. Test set score is calculated to verify the
results.


This example currently does not support parallel execution.
"""

import click
import math

import os
import shutil
import tempfile

from hyperopt import fmin, hp, tpe, rand

import mlflow.projects


@click.command(help="Perform hyperparameter search with Hyperopt library."
                    "Optimize dl_train target.")
@click.option("--max-runs", type=click.INT, default=10,
              help="Maximum number of runs to evaluate.")
@click.option("--epochs", type=click.INT, default=500,
              help="Number of epochs")
@click.option("--metric", type=click.STRING, default="rmse",
              help="Metric to optimize on.")
@click.option("--algo", type=click.STRING, default="tpe.suggest",
              help="Optimizer algorhitm.")
@click.option("--seed", type=click.INT, default=97531,
              help="Seed for the random generator")
@click.option("--training-experiment-id", type=click.INT, default=-1,
              help="Maximum number of runs to evaluate. Inherit parent;s experiment if == -1.")
@click.argument("training_data")
def train(training_data, max_runs, epochs, metric, algo, seed, training_experiment_id):
    """
    Run hyperparameter optimization.
    """
    # create random file to store run ids of the training tasks
    tmp = tempfile.mkdtemp()
    results_path = os.path.join(tmp, "results")
    tracking_client = mlflow.tracking.MlflowClient()

    def new_eval(nepochs,
                 experiment_id,
                 null_train_loss,
                 null_valid_loss,
                 null_test_loss,
                 return_all=False):
        """
        Create a new eval function

        :param nepochs: Number of epochs to train the model.
        :experiment_id: Experiment id for the training run
        :valid_null_loss: Loss of a null model on the validation dataset
        :test_null_loss: Loss of a null model on the test dataset.
        :return_test_loss: Return both validation and test loss if set.

        :return: new eval function.
        """

        def eval(params):
            """
            Train Keras model with given parameters by invoking MLflow run.

            Notice we store runUuid and resulting metric in a file. We will later use these to pick
            the best run and to log the runUuids of the child runs as an artifact. This is a
            temporary workaround until MLflow offers better mechanism of linking runs together.

            :param params: Parameters to the train_keras script we optimize over:
                          learning_rate, drop_out_1
            :return: The metric value evaluated on the validation data.
            """
            import mlflow.tracking
            lr, momentum = params
            p = mlflow.projects.run(
                uri=".",
                entry_point="train",
                parameters={
                    "training_data": training_data,
                    "epochs": str(nepochs),
                    "learning_rate": str(lr),
                    "momentum": str(momentum),
                    "seed": seed},
                experiment_id=experiment_id
            )

            if p.wait():
                training_run = tracking_client.get_run(p.run_id)

                def get_metric(metric_name):
                    return training_run.data.metrics[metric_name].value

                # cap the loss at the loss of the null model
                train_loss = min(null_train_loss,
                                 get_metric("train_{}".format(metric)))
                valid_loss = min(null_valid_loss,
                                 get_metric("val_{}".format(metric)))
                test_loss = min(null_test_loss,
                                get_metric("test_{}".format(metric)))
            else:
                # run failed => return null loss
                tracking_client.set_terminated(p.run_id, "FAILED")
                train_loss = null_train_loss
                valid_loss = null_valid_loss
                test_loss = null_test_loss

            mlflow.log_metric("train_{}".format(metric), train_loss)
            mlflow.log_metric("val_{}".format(metric), valid_loss)
            mlflow.log_metric("test_{}".format(metric), test_loss)

            with open(results_path, "a") as f:
                f.write("{runId} {train} {val} {test}\n".format(runId=p.run_id,
                                                                train=train_loss,
                                                                val=valid_loss,
                                                                test=test_loss))
            if return_all:
                return train_loss, valid_loss, test_loss
            else:
                return valid_loss

        return eval

    space = [
        hp.uniform('lr', 1e-5, 1e-1),
        hp.uniform('momentum', .0, 1.0),
    ]

    with mlflow.start_run() as run:
        experiment_id = run.info.experiment_id if training_experiment_id == -1 \
            else training_experiment_id
        # Evaluate null model first.
        train_null_loss, valid_null_loss, test_null_loss = new_eval(0,
                                                                    experiment_id,
                                                                    math.inf,
                                                                    math.inf,
                                                                    math.inf,
                                                                    True)(params=[0, 0])
        best = fmin(fn=new_eval(epochs,
                                experiment_id,
                                train_null_loss,
                                valid_null_loss,
                                test_null_loss),
                    space=space,
                    algo=tpe.suggest if algo == "tpe.suggest" else rand.suggest,
                    max_evals=max_runs)
        print("best", best)
        best_val_train = math.inf
        best_val_valid = math.inf
        best_val_test = math.inf
        best_run = None
        # we do not have tags yet, for now store list of executed runs as an artifact
        mlflow.log_artifact(results_path, "training_runs")
        with open(results_path) as f:
            for line in f.readlines():
                run_id, str_val, str_val2, str_val3 = line.split(" ")
                val = float(str_val2)
                if val < best_val_valid:
                    best_val_train = float(str_val)
                    best_val_valid = val
                    best_val_test = float(str_val3)
                    best_run = run_id
        # record which run produced the best results, store it as a param for now
        best_run_path = os.path.join(os.path.join(tmp, "best_run.txt"))
        with open(best_run_path, "w") as f:
            f.write("{run_id} {val}\n".format(run_id=best_run, val=best_val_valid))
        mlflow.log_artifact(best_run_path, "best-run")
        mlflow.log_metric("train_{}".format(metric), best_val_train)
        mlflow.log_metric("val_{}".format(metric), best_val_valid)
        mlflow.log_metric("test_{}".format(metric), best_val_test)
        shutil.rmtree(tmp)


if __name__ == '__main__':
    train()
