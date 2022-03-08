"""
Interactions widget
"""
from operator import attrgetter
from itertools import chain

import numpy as np

from AnyQt.QtCore import Qt
from AnyQt.QtGui import QStandardItem

from Orange.data import Table, Domain, ContinuousVariable, StringVariable
from Orange.widgets import gui
from Orange.widgets.utils.itemmodels import DomainModel
from Orange.widgets.utils.signals import Input, Output
from Orange.widgets.utils.widgetpreview import WidgetPreview
from Orange.widgets.widget import OWWidget, AttributeList, Msg
from Orange.preprocess import Discretize, Remove
from Orange.preprocess.discretize import EqualFreq
import Orange.widgets.data.owcorrelations


SIZE_LIMIT = 1000000


class Interaction:
	def __init__(self, disc_data):
		self.data = disc_data
		self.n_attrs = len(self.data.domain.attributes)
		self.class_h = self.entropy(self.data.Y)
		self.attr_h = np.zeros(self.n_attrs)
		self.gains = np.zeros(self.n_attrs)
		self.removed_h = np.zeros((self.n_attrs, self.n_attrs))
		self.compute_gains()

	@staticmethod
	def distribution(ar):
		nans = np.isnan(ar)
		if nans.any():
			if len(ar.shape) == 1:
				ar = ar[~nans]
			else:
				ar = ar[~nans.any(axis=1)]
		_, counts = np.unique(ar, return_counts=True, axis=0)
		return counts / len(ar)

	def entropy(self, ar):
		p = self.distribution(ar)
		return -np.sum(p * np.log2(p))

	def compute_gains(self):
		for attr in range(self.n_attrs):
			self.attr_h[attr] = self.entropy(self.data.X[:, attr])
			self.gains[attr] = self.attr_h[attr] + self.class_h \
				- self.entropy(np.c_[self.data.X[:, attr], self.data.Y])

	def __call__(self, attr1, attr2):
		attrs = np.c_[self.data.X[:, attr1], self.data.X[:, attr2]]
		self.removed_h[attr1, attr2] = self.entropy(attrs) + self.class_h - self.entropy(np.c_[attrs, self.data.Y])
		return self.removed_h[attr1, attr2] - self.gains[attr1] - self.gains[attr2]


class Heuristic:
	def __init__(self, weights):
		self.weights = weights
		self.n_attributes = len(self.weights)
		self.attributes = np.arange(self.n_attributes)
		self.attributes = self.attributes[np.argsort(self.weights)]

	def generate_states(self):
		# prioritize two mid ranked attributes over highest first
		for s in range(1, self.n_attributes * (self.n_attributes - 1) // 2):
			for i in range(max(s - self.n_attributes + 1, 0), (s + 1) // 2):
				yield self.attributes[i], self.attributes[s - i]

	def get_states(self, initial_state):
		states = self.generate_states()
		if initial_state is not None:
			while next(states) != initial_state:
				pass
			return chain([initial_state], states)
		return states


class InteractionRank(Orange.widgets.data.owcorrelations.CorrelationRank):
	IntRole = next(gui.OrangeUserRole)
	RemovedRole = next(gui.OrangeUserRole)

	def __init__(self, *args):
		super().__init__(*args)
		self.interaction = None

	def initialize(self):
		super(Orange.widgets.data.owcorrelations.CorrelationRank, self).initialize()
		data = self.master.disc_data
		self.attrs = data and data.domain.attributes
		self.model_proxy.setFilterKeyColumn(-1)
		self.heuristic = None
		self.use_heuristic = False
		self.sel_feature_index = self.master.feature and data.domain.index(self.master.feature)
		if data:
			self.interaction = Interaction(data)
			self.use_heuristic = len(data) * len(self.attrs) ** 2 > SIZE_LIMIT
			if self.use_heuristic and not self.sel_feature_index:
				self.heuristic = Heuristic(self.interaction.gains)

	def compute_score(self, state):
		attr1, attr2 = state
		h = self.interaction.class_h
		score = self.interaction(attr1, attr2) / h
		gain1 = self.interaction.gains[attr1] / h
		gain2 = self.interaction.gains[attr2] / h
		removed = self.interaction.removed_h[attr1, attr2] / h
		return -score, score, gain1, gain2, removed

	def row_for_state(self, score, state):
		attrs = sorted((self.attrs[x] for x in state), key=attrgetter("name"))
		attr_items = []
		for i, attr in enumerate(attrs):
			item = QStandardItem(attr.name)
			item.setData(attrs, self._AttrRole)
			item.setData(Qt.AlignLeft + Qt.AlignCenter, Qt.TextAlignmentRole)
			item.setToolTip("{}\nInfo Gain: {:.1f}%".format(attr.name, 100*score[2+i]))
			attr_items.append(item)
		interaction_item = QStandardItem("{:+.1f}%".format(100*score[1]))
		interaction_item.setData(score[1], self.IntRole)
		interaction_item.setData(score[4], self.RemovedRole)
		interaction_item.setData(attrs, self._AttrRole)
		interaction_item.setData(
			self.NEGATIVE_COLOR if score[1] < 0 else self.POSITIVE_COLOR,
			gui.TableBarItem.BarColorRole)
		interaction_item.setToolTip("Entropy removed: {:.1f}%".format(100*score[4]))
		return [interaction_item] + attr_items

	def check_preconditions(self):
		return self.master.disc_data is not None


class OWInteractions(Orange.widgets.data.owcorrelations.OWCorrelations):
	name = "Interactions"
	description = "Compute all pairwise attribute interactions."
	category = None

	class Inputs:
		data = Input("Data", Table)

	class Outputs:
		features = Output("Features", AttributeList)
		interactions = Output("Interactions", Table)

	class Warning(OWWidget.Warning):
		not_enough_vars = Msg("At least two features are needed.")
		not_enough_inst = Msg("At least two instances are needed.")
		no_class_var = Msg("Target feature missing")

	def __init__(self):
		OWWidget.__init__(self)
		self.data = None  # type: Table
		self.disc_data = None  # type: Table

		# GUI
		box = gui.vBox(self.controlArea)
		self.feature_model = DomainModel(
			order=DomainModel.ATTRIBUTES, separators=False,
			placeholder="(All combinations)")
		gui.comboBox(
			box, self, "feature", callback=self._feature_combo_changed,
			model=self.feature_model
		)

		self.vizrank, _ = InteractionRank.add_vizrank(
			None, self, None, self._vizrank_selection_changed)
		self.vizrank.button.setEnabled(False)
		self.vizrank.threadStopped.connect(self._vizrank_stopped)

		gui.separator(box)
		box.layout().addWidget(self.vizrank.filter)
		box.layout().addWidget(self.vizrank.rank_table)

		button_box = gui.hBox(self.buttonsArea)
		button_box.layout().addWidget(self.vizrank.button)

	@Inputs.data
	def set_data(self, data):
		self.closeContext()
		self.clear_messages()
		self.data = data
		self.disc_data = None
		self.selection = []
		if data is not None:
			if len(data) < 2:
				self.Warning.not_enough_inst()
			elif data.Y.size == 0:
				self.Warning.no_class_var()
			else:
				remover = Remove(Remove.RemoveConstant)
				data = remover(data)
				disc_data = Discretize(method=EqualFreq())(data)
				if remover.attr_results["removed"]:
					self.Information.removed_cons_feat()
				if len(disc_data.domain.attributes) < 2:
					self.Warning.not_enough_vars()
				else:
					self.disc_data = disc_data
		self.feature_model.set_domain(self.disc_data and self.disc_data.domain)
		self.openContext(self.disc_data)
		self.apply()
		self.vizrank.button.setEnabled(self.disc_data is not None)

	def apply(self):
		self.vizrank.initialize()
		if self.disc_data is not None:
			# this triggers self.commit() by changing vizrank selection
			self.vizrank.toggle()
		else:
			self.commit()

	def commit(self):
		if self.data is None or self.disc_data is None:
			self.Outputs.features.send(None)
			self.Outputs.interactions.send(None)
			return

		attrs = [ContinuousVariable("Interaction"), ContinuousVariable("Entropy Removed")]
		metas = [StringVariable("Feature 1"), StringVariable("Feature 2")]
		domain = Domain(attrs, metas=metas)
		model = self.vizrank.rank_model
		x = np.array(
			[[float(model.data(model.index(row, 0), role))
				for role in (InteractionRank.IntRole, InteractionRank.RemovedRole)]
				for row in range(model.rowCount())])
		m = np.array(
			[[a.name for a in model.data(model.index(row, 0), InteractionRank._AttrRole)]
				for row in range(model.rowCount())], dtype=object)
		int_table = Table(domain, x, metas=m)
		int_table.name = "Interactions"

		# data has been imputed; send original attributes
		self.Outputs.features.send(AttributeList(
			[self.data.domain[var.name] for var in self.selection]))
		self.Outputs.interactions.send(int_table)


if __name__ == "__main__":  # pragma: no cover
	WidgetPreview(OWInteractions).run(Table("iris"))