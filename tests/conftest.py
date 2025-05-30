"""Module with test fixtures."""
from __future__ import annotations

import collections
import pathlib
import tempfile
import typing as t
import uuid

import pytest
from aiida.common import exceptions
from aiida.common.datastructures import CalcInfo
from aiida.common.folders import Folder
from aiida.common.links import LinkType
from aiida.engine import CalcJob
from aiida.engine.utils import instantiate_process
from aiida.manage.manager import get_manager
from aiida.orm import CalcJobNode, Computer, FolderData
from aiida.plugins import CalculationFactory, ParserFactory
from aiida_shell import ShellCode

pytest_plugins = 'aiida.tools.pytest_fixtures'


@pytest.fixture(scope='session', autouse=True)
def aiida_profile(aiida_config, aiida_profile_factory):
    """Create and load a profile with RabbitMQ as broker.

    This overrides the ``aiida_profile`` fixture provided by ``aiida-core`` which runs without broker. However, tests
    in this package make use of the daemon which requires a broker.
    """
    with aiida_profile_factory(aiida_config, broker_backend='core.rabbitmq') as profile:
        yield profile


@pytest.fixture
def parse_calc_job(generate_calc_job_node, generate_parser):
    """Mock a ``CalcJobNode``, instantiate a ``Parser`` and then call :meth:`aiida.parsers.Parser.parse_from_node`."""

    def factory(entry_point_name='core.shell', store_provenance=False, filepath_retrieved_temporary=None, inputs=None):
        """Create fixture.

        :param entry_point_name: entry point name to be used for the mocked ``CalcJobNode``.
        :param store_provenace: whether to store the provenance of the parsing.
        :param filepath_retrieved_temporary: path to temporary retrieved folder.
        :param inputs: dictionary of inputs to add to the mocked ``CalcJobNode``.
        :returns: tuple of the mocked ``CalcJobNode``, the parsed results and the calcfunction node representing the
            parsing action.
        """
        node = generate_calc_job_node(inputs=inputs)
        parser = generate_parser(entry_point_name)
        results, calcfunction = parser.parse_from_node(
            node, store_provenance=store_provenance, retrieved_temporary_folder=filepath_retrieved_temporary
        )
        return node, results, calcfunction

    return factory


@pytest.fixture
def generate_calc_job(tmp_path_factory):
    """Create a :class:`aiida.engine.CalcJob` instance with the given inputs.

    The fixture will call ``prepare_for_submission`` and return a tuple of the temporary folder that was passed to it,
    as well as the ``CalcInfo`` instance that it returned.
    """

    def factory(
        entry_point_name: str,
        inputs: dict[str, t.Any] | None = None,
        return_process: bool = False,
        presubmit: bool = False,
    ) -> tuple[pathlib.Path, CalcInfo] | CalcJob:
        """Create a :class:`aiida.engine.CalcJob` instance with the given inputs.

        :param entry_point_name: The entry point name of the calculation job plugin to run.
        :param inputs: The dictionary of inputs for the calculation job.
        :param return_process: Flag, if ``True``, return the constructed ``CalcJob`` instance instead of the tuple of
            the temporary folder and ``CalcInfo`` instance.
        :param presubmit: Flag, if ``True``, execute ``CalcJob.presubmit`` instead of ``CalcJob.prepare_for_submission``
            which ensures that all input files are written, including those by the scheduler plugin, such as the
            submission script.
        """
        tmp_path = tmp_path_factory.mktemp('calc_job_submit_dir')
        manager = get_manager()
        runner = manager.get_runner()

        process_class: t.Type['CalcJob'] = CalculationFactory(entry_point_name)  # type: ignore[assignment]
        process: CalcJob = instantiate_process(runner, process_class, **inputs or {})  # type: ignore[assignment]

        if presubmit:
            calc_info = process.presubmit(Folder(tmp_path))
        else:
            calc_info = process.prepare_for_submission(Folder(tmp_path))

        if return_process:
            return process

        return tmp_path, calc_info

    return factory


@pytest.fixture
def generate_calc_job_node(generate_computer):
    """Create and return a :class:`aiida.orm.CalcJobNode` instance."""

    def flatten_inputs(inputs, prefix=''):
        """Flatten inputs recursively like :meth:`aiida.engine.processes.process::Process._flatten_inputs`."""
        flat_inputs = []
        for key, value in inputs.items():
            if isinstance(value, collections.abc.Mapping):
                flat_inputs.extend(flatten_inputs(value, prefix=prefix + key + '__'))
            else:
                flat_inputs.append((prefix + key, value))
        return flat_inputs

    def factory(filepath_retrieved: pathlib.Path | None = None, inputs: dict | None = None):
        """Create and return a :class:`aiida.orm.CalcJobNode` instance."""
        node = CalcJobNode(computer=generate_computer(), process_type='aiida.calculations:core.shell')
        node.set_retrieve_list(['stdout'])

        if inputs:
            for link_label, input_node in flatten_inputs(inputs):
                input_node.store()
                node.base.links.add_incoming(input_node, link_type=LinkType.INPUT_CALC, link_label=link_label)

        node.store()
        retrieved = FolderData()

        if filepath_retrieved:
            retrieved.put_object_from_tree(filepath_retrieved)

        retrieved.base.links.add_incoming(node, link_type=LinkType.CREATE, link_label='retrieved')
        retrieved.store()

        return node

    return factory


@pytest.fixture
def generate_computer():
    """Return a :class:`aiida.orm.Computer` instance, either already existing or created."""

    def factory(label='localhost', hostname='localhost', scheduler_type='core.direct', transport_type='core.local'):
        """Return a :class:`aiida.orm.Computer` instance, either already existing or created."""
        try:
            computer = Computer.collection.get(
                label=label, hostname=hostname, scheduler_type=scheduler_type, transport_type=transport_type
            )
        except exceptions.NotExistent:
            computer = Computer(
                label=label,
                hostname=hostname,
                scheduler_type=scheduler_type,
                transport_type=transport_type,
                workdir=tempfile.gettempdir(),
            ).store()

        computer.configure(safe_interval=0.0)
        computer.set_minimum_job_poll_interval(0.0)
        computer.set_default_mpiprocs_per_machine(1)

        return computer

    return factory


@pytest.fixture
def generate_code(generate_computer):
    """Return a :class:`aiida_shell.data.code.ShellCode` instance, either already existing or created."""

    def factory(command='/bin/true', computer_label='localhost', label=None, entry_point_name='core.shell'):
        """Return a :class:`aiida_shell.data.code.ShellCode` instance, either already existing or created."""
        label = label or str(uuid.uuid4())
        computer = generate_computer(computer_label)

        with computer.get_transport() as transport:
            status, stdout, stderr = transport.exec_command_wait(f'which {command}')
            executable = stdout.strip()

            if status != 0:
                raise ValueError(f'failed to determine the absolute path of the command on the computer: {stderr}')

        try:
            filters = {'label': label, 'attributes.input_plugin_name': entry_point_name}
            return ShellCode.collection.get(**filters)
        except exceptions.NotExistent:
            return ShellCode(
                label=label, computer=computer, filepath_executable=executable, default_calc_job_plugin=entry_point_name
            ).store()

    return factory


@pytest.fixture(scope='session')
def generate_parser():
    """Load and return a :class:`aiida.parsers.Parser` from an entry point."""

    def factory(entry_point_name):
        """Load and return a :class:`aiida.parsers.Parser` from an entry point.

        :param entry_point_name: entry point name of the parser class.
        :return: the loaded parser plugin.
        """
        return ParserFactory(entry_point_name)

    return factory
