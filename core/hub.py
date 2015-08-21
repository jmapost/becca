""" 
the Hub class 
"""
import numpy as np
import tools

class Hub(object):
    """ 
    The central action valuation mechanism 
    
    The analogy of the hub and spoke stucture is suggested by 
    the fact that the hub has a connection to each
    of the gearboxes. In the course of each timestep it 
    1) reads in a copy of the input cable activities to each of the gearboxes
    2) updates the reward estimate for current transitions and
    3) selects a goal.
    """
    def __init__(self, num_cables, num_actions=0, num_sensors=0, 
                 exploit=False, classifier=False, name='hub'):
        self.name = name
        self.num_cables = num_cables
        self.num_actions = num_actions
        self.classifier = classifier
        self.exploit = exploit
        # Tweak the rate of decay of the reward trace
        self.TIME_FACTOR = 1. 
        # Set constants that adjust the behavior of the hub
        if self.classifier:
            self.REWARD_LEARNING_RATE = 1e-5
            self.TRACE_LENGTH = 1
        else:
            self.REWARD_LEARNING_RATE = 1e-3
            self.TRACE_LENGTH = int(self.TIME_FACTOR * 6.)
            
        # As time passes, grow more optimistic about the effect of 
        # trying neglected goals.
        # A curiosity time constant. Larger means curiosity builds more slowly.
        self.EXPLORATION_FACTOR = 10. * self.num_cables**.5 * self.num_actions
        # Keep a history of reward and active features to account for 
        # delayed reward.
        self.trace_magnitude = 0.
        for tau in np.arange(self.TRACE_LENGTH):
            self.trace_magnitude += 2. ** (-self.TIME_FACTOR * float(tau))

        # Initialize variables for later use
        self.reward_history = list(np.zeros(self.TRACE_LENGTH))
        feature_shape = self.num_cables
        self.activity_history = [np.zeros(feature_shape)] * (
                self.TRACE_LENGTH + 1)
        self.action_history = [np.zeros((self.num_actions, 1))] * (
                self.TRACE_LENGTH + 1)
        """
        The mask indicates which cables are active and which are 
        yet unused. Ignoring the ones that haven't started carrying
        a signal yet speeds up learning.
        """
        self.mask = np.zeros(feature_shape)
        #self.mask[:num_actions + num_sensors] = 1.
        self.mask[:num_sensors] = 1.
        """
        reward is a property of every transition. 
        In this 2D array representation, each row
        represents a feature, and each column represents 
        a goal (action). Element [i,j] represents the transition
        from feature i to action j.
        """
        transition_shape = (self.num_cables, self.num_actions)
        self.reward = np.zeros(transition_shape)
        # This counts the number of times each transition has been encountered 
        self.count = np.zeros(transition_shape)
        # A running total of a feature's activity, 
        # since a given transition was tried
        self.running_activity = np.zeros(transition_shape)
        self.frame_counter = 10000

    def step(self, cable_activities, raw_reward, modified_cables):
        """ 
        Advance the hub one step 
        """
        # Update the reward trace, a decayed sum of recent rewards.
        reward_trace = 0.
        for tau in range(self.TRACE_LENGTH):
            """
            Work from the end of the list, from the most recent
            to the oldest, decaying future values the further
            they are away from the cause and effect that occurred
            TRACE_LENGTH time steps ago. The decay function here
            is hyperbolic, rather than exponential. It has a basis
            in both experimental psychology and economics as representing
            typical human behavior.
            """
            reward_trace += self.reward_history[tau] * (
                    2. ** (-self.TIME_FACTOR * float(tau)))
        reward_trace /= self.trace_magnitude

        self.mask = (np.sign(np.maximum(self.mask, cable_activities))
                     ).astype('int')
        state = self.activity_history[0][:,np.newaxis]
        # debug: Remove actions from features
        # Don't train on deliberate actions
        #state[:self.num_actions] = 0.
        action = self.action_history[0]
        
        # Increment the count according to the most recent features and actions
        state_by_action = state * action.T 
        self.count += state_by_action
        # Reset the count for all modified cables
        if len(modified_cables) > 0: 
            self.count[np.array(modified_cables), :] = 0.
        if self.exploit:
            # Aim for highest performance possible and do no exploration
            self.curiosity = np.zeros(self.count.shape)
        else:
            #self.curiosity = self.running_activity / (self.running_activity + 
            #        self.EXPLORATION_FACTOR * (1. + self.count) )
            #print self.running_activity
            self.curiosity = self.running_activity / (self.running_activity + 
                                                      self.EXPLORATION_FACTOR)

        if not self.exploit:
            # Update the expected reward.
            # If exploiting, don't do this. It allows BECCA to glean
            # valuable training information from repeated exposure to the
            # same test instance.
            rate_raw = ((1 - self.REWARD_LEARNING_RATE) / 
                        (self.count + tools.EPSILON) + 
                        self.REWARD_LEARNING_RATE) 
            rate = np.minimum(0.5, rate_raw)
            self.reward += ((reward_trace - self.reward) * 
                             state_by_action * rate) 

        self.reward_trace = reward_trace
        self.state_by_action = state_by_action

        # Choose a goal at every timestep
        average_reward = np.average(self.reward, axis=0,
                                    weights=cable_activities.ravel() + 
                                    tools.EPSILON)
        average_curiosity = np.average(self.curiosity, axis=0, 
                                       weights=cable_activities.ravel() + 
                                       tools.EPSILON)
        expected_reward = average_reward + average_curiosity
        # Ignore all cables that are still masked. These are yet unused.
        best_reward = np.max(expected_reward)
        potential_winners = np.where(expected_reward == best_reward)[0] 
        # Break any ties by lottery
        if potential_winners.size > 0:
            goal_cable = potential_winners[np.random.randint(
                    potential_winners.size)]
        else:
            goal_cable = np.random.randint(self.num_actions)

        #print 'gc', goal_cable, 'ars', average_reward.shape
        #print 'rs', self.reward.shape, 'cas', cable_activities.shape
        hub_reward = average_reward[goal_cable]
        hub_curiosity = average_curiosity[goal_cable]

        """
        Instituting this check here biases the solution.
        It will work faster on certain types of worlds, and fail 
        entirely on others. Having no check is the most
        robust way to proceed.

        if best_reward >= raw_reward * self.trace_magnitude:
            return goal_cable, hub_reward, hub_curiosity
        else:
            return None, 0., 0.
        """
        return goal_cable, hub_reward, hub_curiosity, reward_trace
       
    def update(self, cable_activities, issued_goal_index, raw_reward): 
        """ 
        Assign the goal to train on, based on the goal that was issued 
        """
        goal = np.zeros((self.num_actions, 1))
        if issued_goal_index is not None:
            goal[issued_goal_index] = 1.
        active_cables = np.where(cable_activities > 0.)[0]
        #print 'issued_goal_index'
        #print issued_goal_index 
        #print 'active cables'
        #print active_cables
        #print 'pre-updated running activity', self.running_activity.shape
        #print self.running_activity[active_cables,:]
        self.running_activity  = (self.running_activity + 
                                  cable_activities[:,np.newaxis])
        self.running_activity[:,issued_goal_index] *=  np.maximum(
                0., 1. - cable_activities)
        # Update the activity, action history, and reward.
        self.reward_history.append(raw_reward)
        self.reward_history.pop(0)
        self.activity_history.append(np.copy(cable_activities))
        self.activity_history.pop(0)
        self.action_history.append(np.copy(goal))
        self.action_history.pop(0)

    def add_cables(self, num_new_cables):
        """ 
        Add new cables to the hub when new gearboxes are created 
        """ 
        self.num_cables = self.num_cables + num_new_cables
        features_shape = self.num_cables
        transition_shape = (self.num_cables, self.num_actions) 
        self.reward = tools.pad(self.reward, transition_shape)
        self.count = tools.pad(self.count, transition_shape)
        self.running_activity = tools.pad(self.running_activity, 
                                              transition_shape)
        self.mask = tools.pad(self.mask, features_shape)
        for index in range(len(self.activity_history)):
            self.activity_history[index] = tools.pad(
                    self.activity_history[index], features_shape)
            self.action_history[index] = tools.pad(
                    self.action_history[index], (self.num_actions, 1))
