import logging
import numpy as np

import tensorflow as tf
from mlagents.trainers.models import LearningModel

logger = logging.getLogger("mlagents.envs")


class PPOModel(LearningModel):
    def __init__(self, brain, lr=1e-4, h_size=128, epsilon=0.2, beta=1e-3, max_step=5e6,
                 normalize=False, use_recurrent=False, num_layers=2, m_size=None,
                 use_curiosity=False, seed=0):
        """
        Takes a Unity environment and model-specific hyper-parameters and returns the
        appropriate PPO agent model for the environment.
        :param brain: BrainInfo used to generate specific network graph.
        :param lr: Learning rate.
        :param h_size: Size of hidden layers
        :param epsilon: Value for policy-divergence threshold.
        :param beta: Strength of entropy regularization.
        :return: a sub-class of PPOAgent tailored to the environment.
        :param max_step: Total number of training steps.
        :param normalize: Whether to normalize vector observation input.
        :param use_recurrent: Whether to use an LSTM layer in the network.
        :param num_layers Number of hidden layers between encoded input and policy & value layers
        :param m_size: Size of brain memory.
        """
        LearningModel.__init__(self, m_size, normalize, use_recurrent, brain, seed)
        self.use_curiosity = use_curiosity
        if num_layers < 1:
            num_layers = 1
        self.last_reward, self.new_reward, self.update_reward = self.create_reward_encoder()
        if brain.vector_action_space_type == "continuous":
            self.create_cc_actor_critic(h_size, num_layers)
            self.entropy = tf.ones_like(tf.reshape(self.value, [-1])) * self.entropy
        else:
            self.create_dc_actor_critic(h_size, num_layers)
        self.create_losses(self.log_probs, self.old_log_probs, self.value,
                                  self.entropy, beta, epsilon, lr, max_step)

    @staticmethod
    def create_reward_encoder():
        """Creates TF ops to track and increment recent average cumulative reward."""
        last_reward = tf.Variable(0, name="last_reward", trainable=False, dtype=tf.float32)
        new_reward = tf.placeholder(shape=[], dtype=tf.float32, name='new_reward')
        update_reward = tf.assign(last_reward, new_reward)
        return last_reward, new_reward, update_reward

    def create_losses(self, probs, old_probs, value, entropy, beta, epsilon, lr, max_step):
        """
        Creates training-specific Tensorflow ops for PPO models.
        :param probs: Current policy probabilities
        :param old_probs: Past policy probabilities
        :param value: Current value estimate
        :param beta: Entropy regularization strength
        :param entropy: Current policy entropy
        :param epsilon: Value for policy-divergence threshold
        :param lr: Learning rate
        :param max_step: Total number of training steps.
        """
        self.returns_holder = tf.placeholder(shape=[None], dtype=tf.float32,
                                             name='discounted_rewards')
        self.advantage = tf.placeholder(shape=[None, 1], dtype=tf.float32, name='advantages')
        self.learning_rate = tf.train.polynomial_decay(lr, self.global_step, max_step, 1e-10,
                                                       power=1.0)

        self.old_value = tf.placeholder(shape=[None], dtype=tf.float32, name='old_value_estimates')

        decay_epsilon = tf.train.polynomial_decay(epsilon, self.global_step, max_step, 0.1,
                                                  power=1.0)
        decay_beta = tf.train.polynomial_decay(beta, self.global_step, max_step, 1e-5, power=1.0)

        clipped_value_estimate = self.old_value + tf.clip_by_value(
            tf.reduce_sum(value, axis=1) - self.old_value,
            - decay_epsilon, decay_epsilon)

        v_opt_a = tf.squared_difference(self.returns_holder, tf.reduce_sum(value, axis=1))
        v_opt_b = tf.squared_difference(self.returns_holder, clipped_value_estimate)
        self.value_loss = tf.reduce_mean(
            tf.dynamic_partition(tf.maximum(v_opt_a, v_opt_b), self.mask, 2)[1])

        r_theta = tf.exp(probs - old_probs)
        p_opt_a = r_theta * self.advantage
        p_opt_b = tf.clip_by_value(r_theta, 1.0 - decay_epsilon,
                                   1.0 + decay_epsilon) * self.advantage
        self.policy_loss = -tf.reduce_mean(
            tf.dynamic_partition(tf.minimum(p_opt_a, p_opt_b), self.mask, 2)[1])

        self.loss = self.policy_loss + 0.5 * self.value_loss - decay_beta * tf.reduce_mean(
            tf.dynamic_partition(entropy, self.mask, 2)[1])


    def create_ppo_optimizer(self):
        optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate)
        self.update_batch = optimizer.minimize(self.loss)
