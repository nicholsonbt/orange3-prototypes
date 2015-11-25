import sys
from xml.sax.saxutils import escape
from types import SimpleNamespace as namespace

import numpy
import sklearn.metrics

from PyQt4 import QtGui, QtCore
from PyQt4.QtCore import Qt, QEvent, QRectF, QSizeF
from PyQt4.QtCore import pyqtSignal as Signal
import Orange.data
import Orange.distance

from Orange.widgets import widget, gui, settings
from Orange.widgets.utils import itemmodels
from Orange.widgets.unsupervised.owhierarchicalclustering import \
    WrapperLayoutItem


class OWSilhouettePlot(widget.OWWidget):
    name = "Silhouette Plot"
    description = "Silhouette Plot"

    icon = "icons/Silhouette.svg"

    inputs = [("Data", Orange.data.Table, "set_data")]
    outputs = [("Selected Data", Orange.data.Table),
               ("Other Data", Orange.data.Table)]

    settingsHandler = settings.PerfectDomainContextHandler()

    #: Distance metric index
    distance_idx = settings.Setting(0)
    #: Group/cluster variable index
    cluster_var_idx = settings.ContextSetting(0)
    #: Group the silhouettes by cluster
    group_by_cluster = settings.Setting(True)
    #: A fixed size for an instance bar
    bar_size = settings.Setting(3)
    #: Add silhouette scores to output data
    add_scores = settings.Setting(False)
    auto_commit = settings.Setting(False)

    Distances = [("Euclidean", Orange.distance.Euclidean),
                 ("Manhattan", Orange.distance.Manhattan)]

    def __init__(self):
        super().__init__()

        self.data = None
        self._effective_data = None
        self._matrix = None
        self._silhouette = None
        self._labels = None
        self._silplot = None

        box = gui.widgetBox(self.controlArea, "Settings",)
        gui.comboBox(box, self, "distance_idx", label="Distance",
                     items=[name for name, _ in OWSilhouettePlot.Distances],
                     callback=self._invalidate_distances)
        self.cluster_var_cb = gui.comboBox(
            box, self, "cluster_var_idx", label="Cluster",
            callback=self._invalidate_scores)
        self.cluster_var_model = itemmodels.VariableListModel(parent=self)
        self.cluster_var_cb.setModel(self.cluster_var_model)

        gui.spin(box, self, "bar_size", minv=1, maxv=10, label="Bar Size",
                 callback=self._update_bar_size)

        gui.checkBox(box, self, "group_by_cluster", "Group by cluster",
                     callback=self._replot)

        gui.rubber(self.controlArea)

        box = gui.widgetBox(self.controlArea, "Output")
        gui.checkBox(box, self, "add_scores", "Add silhouette scores",)
        gui.auto_commit(box, self, "auto_commit", "Commit", box=False)

        self.scene = QtGui.QGraphicsScene()
        self.view = QtGui.QGraphicsView(self.scene)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.view.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.mainArea.layout().addWidget(self.view)

    def sizeHint(self):
        sh = self.controlArea.sizeHint()
        return sh.expandedTo(QtCore.QSize(600, 720))

    def set_data(self, data):
        """
        Set the input data set.
        """
        self.closeContext()
        self.clear()
        error_msg = ""
        if data is not None:
            candidatevars = [v for v in data.domain.variables + data.domain.metas
                             if v.is_discrete and len(v.values) >= 2]
            if not candidatevars:
                error_msg = "Input does not have any suitable cluster labels."
                data = None
            else:
                self.cluster_var_model[:] = candidatevars
                if data.domain.class_var in candidatevars:
                    self.cluster_var_idx = candidatevars.index(data.domain.class_var)
                else:
                    self.cluster_var_idx = 0

        self.data = data
        if data is not None:
            self._effective_data = Orange.distance._preprocess(data)
            self.openContext(Orange.data.Domain(candidatevars))

        self.error(0, error_msg)

    def handleNewSignals(self):
        if self._effective_data is not None:
            self._update()
            self._replot()

        self.unconditional_commit()

    def clear(self):
        """
        Clear the widget state.
        """
        self.data = None
        self._effective_data = None
        self._matrix = None
        self.cluster_var_model[:] = []
        self._clear_scene()

    def _clear_scene(self):
        # Clear the graphics scene and associated objects
        self.scene.clear()
        self.scene.setSceneRect(QRectF())
        self._silplot = None

    def _invalidate_distances(self):
        # Invalidate the computed distance matrix and recompute the silhouette.
        self._matrix = None
        self._invalidate_scores()

    def _invalidate_scores(self):
        # Invalidate and recompute the current silhouette scores.
        self._labels = self._silhouette = None
        self._update()
        self._replot()
        if self.data is not None:
            self.commit()

    def _update(self):
        # Update/recompute the distances/scores as required
        if self.data is None:
            self._silhouette = None
            self._labels = None
            self._matrix = None
            self._clear_scene()
            return

        if self._matrix is None and self._effective_data is not None:
            _, metric = self.Distances[self.distance_idx]
            self._matrix = numpy.asarray(metric(self._effective_data))

        labelvar = self.cluster_var_model[self.cluster_var_idx]
        labels, _ = self.data.get_column_view(labelvar)
        labels = labels.astype(int)
        _, counts = numpy.unique(labels, return_counts=True)
        if numpy.count_nonzero(counts) >= 2:
            self.error(1, "")
            silhouette = sklearn.metrics.silhouette_samples(
                self._matrix, labels, metric="precomputed")
        else:
            self.error(1, "Need at least 2 clusters with non zero counts")
            labels = silhouette = None

        self._labels = labels
        self._silhouette = silhouette

    def _replot(self):
        # Clear and replot/initialize the scene
        self._clear_scene()
        if self._silhouette is not None and self._labels is not None:
            var = self.cluster_var_model[self.cluster_var_idx]
            silplot = SilhouettePlot()
            silplot.setBarHeight(self.bar_size)

            if self.group_by_cluster:
                silplot.setScores(self._silhouette, self._labels, var.values)
            else:
                silplot.setScores(
                    self._silhouette,
                    numpy.zeros(len(self._silhouette), dtype=int),
                    [""]
                )
            silplot.resize(silplot.effectiveSizeHint(Qt.PreferredSize))

            self.scene.addItem(silplot)
            self._silplot = silplot
            silplot.selectionChanged.connect(self.commit)
            self.scene.setSceneRect(
                QRectF(QtCore.QPointF(0, 0),
                       self._silplot.effectiveSizeHint(Qt.PreferredSize)))

    def _update_bar_size(self):
        if self._silplot is not None:
            self._silplot.setBarHeight(self.bar_size)
            self.scene.setSceneRect(
                QRectF(QtCore.QPointF(0, 0),
                       self._silplot.effectiveSizeHint(Qt.PreferredSize)))

    def commit(self):
        """
        Commit/send the current selection to the output.
        """
        selected = other = None
        if self.data is not None:
            selectedmask = numpy.full(len(self.data), False, dtype=bool)
            if self._silplot is not None:
                indices = self._silplot.selection()
                selectedmask[indices] = True
            scores = self._silhouette
            silhouette_var = None
            if self.add_scores:
                var = self.cluster_var_model[self.cluster_var_idx]
                silhouette_var = Orange.data.ContinuousVariable(
                    "Silhouette ({})".format(escape(var.name)))
                domain = Orange.data.Domain(
                    self.data.domain.attributes,
                    self.data.domain.class_vars,
                    self.data.domain.metas + (silhouette_var, ))
            else:
                domain = self.data.domain

            if numpy.count_nonzero(selectedmask):
                selected = self.data.from_table(
                    domain, self.data, numpy.flatnonzero(selectedmask))

            if numpy.count_nonzero(~selectedmask):
                other = self.data.from_table(
                    domain, self.data, numpy.flatnonzero(~selectedmask))

            if self.add_scores:
                if selected is not None:
                    selected[:, silhouette_var] = numpy.c_[scores[selectedmask]]
                if other is not None:
                    other[:, silhouette_var] = numpy.c_[scores[~selectedmask]]

        self.send("Selected Data", selected)
        self.send("Other Data", other)


class SilhouettePlot(QtGui.QGraphicsWidget):
    """
    A silhouette plot widget.
    """
    #: Emitted when the current selection has changed
    selectionChanged = Signal()

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.__groups = []
        self.__barHeight = 3
        self.__selectionRect = None
        self.__selection = numpy.asarray([], dtype=int)
        self.__pen = QtGui.QPen(Qt.NoPen)
        self.__brush = QtGui.QBrush(QtGui.QColor("#3FCFCF"))
        self.setLayout(QtGui.QGraphicsGridLayout())
        self.layout().setColumnSpacing(0, 1.)

    def setScores(self, scores, labels, values):
        """
        Set the silhouette scores/labels to for display.

        Arguments
        ---------
        scores : (N,) ndarray
            The silhouette scores.
        labels : (N,) ndarray
            A ndarray (dtype=int) of label/clusters indices.
        values : list of str
            A list of label/cluster names.
        """
        scores = numpy.asarray(scores, dtype=float)
        labels = numpy.asarray(labels, dtype=int)
        if not (scores.ndim == labels.ndim == 1):
            raise ValueError("scores and labels must be 1 dimensional")
        if scores.shape != labels.shape:
            raise ValueError("scores and labels must have the same shape")

        Ck = numpy.unique(labels)
        assert Ck[0] >= 0 and Ck[-1] < len(values)
        cluster_indices = [numpy.flatnonzero(labels == i)
                           for i in range(len(values))]
        cluster_indices = [indices[numpy.argsort(scores[indices])[::-1]]
                           for indices in cluster_indices]
        groups = [
            namespace(scores=scores[indices], indices=indices, label=label)
            for indices, label in zip(cluster_indices, values)
        ]
        self.clear()
        self.__groups = groups
        self.__setup()

    def setBarHeight(self, height):
        if height != self.__barHeight:
            self.__barHeight = height
            for item in self.__plotItems():
                item.setPreferredBarSize(height)

    def barHeight(self):
        return self.__barHeight

    def clear(self):
        """
        Clear the widget state
        """
        scene = self.scene()
        for child in self.childItems():
            child.setParentItem(None)
            scene.removeItem(child)
        self.__groups = []

    def __setup(self):
        # Setup the subwidgets/groups/layout
        smax = max((numpy.max(g.scores) for g in self.__groups
                    if g.scores.size),
                   default=1)

        smin = min((numpy.min(g.scores) for g in self.__groups
                    if g.scores.size),
                   default=-1)
        smin = min(smin, 0)

        for i, group in enumerate(self.__groups):
            silhouettegroup = BarPlotItem(parent=self)
            silhouettegroup.setBrush(self.__brush)
            silhouettegroup.setPen(self.__pen)
            silhouettegroup.setDataRange(smin, smax)
            silhouettegroup.setPlotData(group.scores)
            silhouettegroup.setPreferredBarSize(self.__barHeight)
            silhouettegroup.setData(0, group.indices)
            self.layout().addItem(silhouettegroup, i, 2)

            if group.label:
                line = QtGui.QFrame(frameShape=QtGui.QFrame.VLine)
                proxy = QtGui.QGraphicsProxyWidget(self)
                proxy.setWidget(line)
                self.layout().addItem(proxy, i, 1)
                label = QtGui.QGraphicsSimpleTextItem(self)
                label.setText("{} ({})".format(escape(group.label),
                                              len(group.scores)))
                item = WrapperLayoutItem(label, Qt.Vertical, parent=self)
                self.layout().addItem(item, i, 0, Qt.AlignCenter)

    def event(self, event):
        if event.type() == QEvent.LayoutRequest and \
                self.parentLayoutItem() is None:
            self.resize(self.effectiveSizeHint(Qt.PreferredSize))
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            if self.__selectionRect is None:
                self.__selectionRect = QtGui.QGraphicsRectItem(
                    QRectF(event.buttonDownPos(Qt.LeftButton),
                           event.pos()).normalized())
                self.__selectionRect.setParentItem(self)
            self.__selectionRect.setRect(
                QRectF(event.buttonDownPos(Qt.LeftButton),
                       event.pos()).normalized()
                .intersected(self.contentsRect())
            )
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.__selectionRect is not None:
                self.__selectionRect.setParentItem(None)
                if self.scene() is not None:
                    self.scene().removeItem(self.__selectionRect)
                self.__selectionRect = None
            event.accept()

            rect = (QRectF(event.buttonDownPos(Qt.LeftButton), event.pos())
                    .normalized())

            if not rect.isValid():
                rect = rect.adjusted(-0.01, -0.01, 0.01, 0.01)

            rect = rect.intersected(self.contentsRect())
            Clear, Select, Deselect, Toogle = 1, 2, 4, 8

            if event.modifiers() & Qt.ControlModifier:
                saction = Toogle
            elif event.modifiers() & Qt.AltModifier:
                saction = Deselect
            elif event.modifiers() & Qt.ShiftModifier:
                saction = Select
            else:
                saction = Clear | Select

            indices = self.__selectionIndices(rect)

            if saction & Clear:
                selection = []
            else:
                selection = self.__selection
            if saction & Toogle:
                selection = numpy.setxor1d(selection, indices)
            elif saction & Deselect:
                selection = numpy.setdiff1d(selection, indices)
            elif saction & Select:
                selection = numpy.union1d(selection, indices)
            self.setSelection(selection)

    def __selectionIndices(self, rect):
        items = [item for item in self.__plotItems()
                 if item.geometry().intersects(rect)]
        selection = [numpy.array([], dtype=int)]
        for item in items:
            indices = item.data(0)
            itemrect = item.geometry().intersected(rect)
            crect = item.contentsRect()
            itemrect = (item.mapFromParent(itemrect).boundingRect()
                        .intersected(crect))
            assert itemrect.top() >= 0
            rowh = crect.height() / item.count()
            indextop = numpy.floor(itemrect.top() / rowh)
            indexbottom = numpy.ceil(itemrect.bottom() / rowh)
            selection.append(indices[int(indextop): int(indexbottom)])
        return numpy.hstack(selection)

    def __selectionChanged(self, selected, deselected):
        for item, grp in zip(self.__plotItems(), self.__groups):
            select = numpy.flatnonzero(
                numpy.in1d(grp.indices, selected, assume_unique=True))
            items = item.items()
            if select.size:
                for i in select:
                    items[i].setBrush(Qt.red)

            deselect = numpy.flatnonzero(
                numpy.in1d(grp.indices, deselected, assume_unique=True))
            if deselect.size:
                for i in deselect:
                    items[i].setBrush(self.__brush)

    def __plotItems(self):
        for i in range(len(self.__groups)):
            item = self.layout().itemAt(i, 2)
            if item is not None:
                assert isinstance(item, BarPlotItem)
                yield item

    def setSelection(self, indices):
        indices = numpy.unique(numpy.asarray(indices, dtype=int))
        select = numpy.setdiff1d(indices, self.__selection)
        deselect = numpy.setdiff1d(self.__selection, indices)

        self.__selectionChanged(select, deselect)
        self.__selection = indices

        if deselect.size or select.size:
            self.selectionChanged.emit()

    def selection(self):
        return numpy.asarray(self.__selection, dtype=int)


class BarPlotItem(QtGui.QGraphicsWidget):
    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.__barsize = 5
        self.__spacing = 1
        self.__pen = QtGui.QPen(Qt.NoPen)
        self.__brush = QtGui.QBrush(QtGui.QColor("#3FCFCF"))
        self.__range = (0., 1.)
        self.__data = None
        self.__items = []

    def count(self):
        if self.__data is not None:
            return self.__data.size
        else:
            return 0

    def items(self):
        return list(self.__items)

    def setGeometry(self, geom):
        super().setGeometry(geom)
        self.__layout()

    def sizeHint(self, which, constraint=QSizeF()):
        spacing = max(self.__spacing * (self.count() - 1), 0)
        return QSizeF(300, self.__barsize * self.count() + spacing)

    def setPreferredBarSize(self, size):
        if self.__barsize != size:
            self.__barsize = size
            self.updateGeometry()

    def spacing(self):
        return self.__spacing

    def setSpacing(self, spacing):
        if self.__spacing != spacing:
            self.__spacing = spacing
            self.updateGeometry()

    def setPen(self, pen):
        pen = QtGui.QPen(pen)
        if self.__pen != pen:
            self.__pen = pen
            for item in self.__items:
                item.setPen(pen)

    def pen(self):
        return QtGui.QPen(self.__pen)

    def setBrush(self, brush):
        brush = QtGui.QBrush(brush)
        if self.__brush != brush:
            self.__brush = brush
            for item in self.__items:
                item.setBrush(brush)

    def brush(self):
        return QtGui.QBrush(self.__brush)

    def setPlotData(self, values):
        self.__data = numpy.array(values, copy=True)
        self.__update()
        self.updateGeometry()

    def setDataRange(self, rangemin, rangemax):
        if self.__range != (rangemin, rangemax):
            self.__range = (rangemin, rangemax)
            self.__layout()

    def __clear(self):
        for item in self.__items:
            item.setParentItem(None)
        scene = self.scene()
        if scene is not None:
            for item in self.__items:
                scene.removeItem(item)
        self.__items = []

    def __update(self):
        self.__clear()
        if self.__data is None:
            return

        pen = self.pen()
        brush = self.brush()
        for i, v in enumerate(self.__data):
            item = QtGui.QGraphicsRectItem(self)
            item.setPen(pen)
            item.setBrush(brush)
            self.__items.append(item)

        self.__layout()
        return

    def __layout(self):
        if self.__data is None:
            return

        (N, ) = self.__data.shape
        if not N:
            return

        spacing = self.__spacing
        rect = self.contentsRect()
        w = rect.width()
        if rect.height() - (spacing * (N - 1)) <= 0:
            spacing = 0

        h = (rect.height() - (spacing * (N - 1))) / N
        xmin, xmax = self.__range
        span = xmax - xmin
        if span < 1e-9:
            span = 1
        scalef = w * 1 / span

        base = 0
        base = (base - xmin) * scalef
        datascaled = (self.__data - xmin) * scalef

        for i, (v, item) in enumerate(zip(datascaled, self.__items)):
            item.setRect(QRectF(base, rect.top() + i * (h + spacing),
                                v - base, h).normalized())


def main(argv=sys.argv):
    app = QtGui.QApplication(list(sys.argv))
    argv = app.argv()
    if len(argv) > 1:
        filename = argv[1]
    else:
        filename = "iris"
    w = OWSilhouettePlot()
    w.show()
    w.raise_()
    w.set_data(Orange.data.Table(filename))
    w.handleNewSignals()
    app.exec_()
    w.set_data(None)
    w.handleNewSignals()
    w.onDeleteWidget()
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))