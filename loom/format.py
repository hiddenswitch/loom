# Copyright (c) 2014, Salesforce.com, Inc.  All rights reserved.
# Copyright (c) 2015, Google, Inc.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# - Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
# - Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
# - Neither the name of Salesforce.com nor the names of its contributors
#   may be used to endorse or promote products derived from this
#   software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import os
import shutil
from itertools import cycle
from itertools import izip
from contextlib2 import ExitStack
from collections import defaultdict
import parsable
from distributions.dbg.models import dpd
from distributions.fileutil import tempdir
from distributions.io.stream import json_dump
from distributions.io.stream import json_load
from distributions.io.stream import open_compressed
from distributions.io.stream import protobuf_stream_load
from loom.util import csv_reader
from loom.util import csv_writer
from loom.util import LoomError
import loom.util
import loom.schema
import loom.schema_pb2
import loom.cFormat
import loom.documented
parsable = parsable.Parsable()

OTHER_DECODE = '_OTHER'

TRUTHY = ['1', '1.0', 'True', 'true', 't']
FALSEY = ['0', '0.0', 'False', 'false', 'f']
BOOLEAN_SYMBOLS = {
    key: value
    for keys, value in [(TRUTHY, True), (FALSEY, False)]
    for key in keys
}

EXAMPLE_VALUES = {
    'booleans': False,
    'counts': 0,
    'reals': 0.0,
}

EXAMPLE_CATEGORICAL_ENCODER = {
    'name': 'day-of-week',
    'model': 'dd',
    'symbols': {
        'Monday': 0,
        'Tuesday': 1,
        'Wednesday': 2,
        'Friday': 4,
    },
}


@parsable.command
@loom.documented.transform(
    inputs=['ingest.schema'],
    outputs=['ingest.schema_row'])
def make_schema_row(schema_in, schema_row_out):
    '''
    Convert json schema to protobuf schema row.
    '''
    schema = json_load(schema_in)
    if not schema:
        raise LoomError('Schema is empty: {}'.format(schema_in))
    value = loom.schema_pb2.ProductValue()
    value.observed.sparsity = loom.schema_pb2.ProductValue.Observed.DENSE
    for model in schema.itervalues():
        try:
            field = loom.schema.MODEL_TO_DATATYPE[model]
        except KeyError:
            raise LoomError('Unknown model {} in schema {}'.format(
                model, schema_in))
        value.observed.dense.append(True)
        getattr(value, field).append(EXAMPLE_VALUES[field])
    with open_compressed(schema_row_out, 'wb') as f:
        f.write(value.SerializeToString())


class DefaultEncoderBuilder(object):
    def __init__(self, name, model):
        self.name = name
        self.model = model

    def add_value(self, value):
        pass

    def __iadd__(self, other):
        pass

    def build(self):
        return {
            'name': self.name,
            'model': self.model,
        }


class CategoricalEncoderBuilder(object):
    def __init__(self, name, model):
        self.name = name
        self.model = model
        self.counts = defaultdict(lambda: 0)

    def add_value(self, value):
        self.counts[value] += 1

    def __iadd__(self, other):
        for key, value in other.counts.iteritems():
            self.counts[key] += value

    def build(self):
        sorted_keys = [(-count, key) for key, count in self.counts.iteritems()]
        sorted_keys.sort()
        symbols = {key: i for i, (_, key) in enumerate(sorted_keys)}
        if self.model == 'dpd':
            assert 'OTHER_DECODE not in symbols', \
                   'data cannot assume reserved value {}'.format(OTHER_DECODE)
            symbols[OTHER_DECODE] = dpd.OTHER
        return {
            'name': self.name,
            'model': self.model,
            'symbols': symbols,
        }

    def __getstate__(self):
        return (self.name, self.model, dict(self.counts))

    def __setstate__(self, (name, model, counts)):
        self.name = name
        self.model = model
        self.counts = defaultdict(lambda: 0)
        self.counts.update(counts)


ENCODER_BUILDERS = defaultdict(lambda: DefaultEncoderBuilder)
ENCODER_BUILDERS['dd'] = CategoricalEncoderBuilder
ENCODER_BUILDERS['dpd'] = CategoricalEncoderBuilder


class CategoricalFakeEncoderBuilder(object):
    def __init__(self, name, model):
        self.name = name
        self.model = model
        self.max_value = -1

    def add_value(self, value):
        self.max_value = max(self.max_value, int(value))

    def build(self):
        symbols = {int(value): value for value in xrange(self.max_value + 1)}
        if self.model == 'dpd':
            symbols[OTHER_DECODE] = dpd.OTHER
        return {
            'name': self.name,
            'model': self.model,
            'symbols': symbols,
        }


FAKE_ENCODER_BUILDERS = defaultdict(lambda: DefaultEncoderBuilder)
FAKE_ENCODER_BUILDERS['dd'] = CategoricalFakeEncoderBuilder
FAKE_ENCODER_BUILDERS['dpd'] = CategoricalFakeEncoderBuilder


def load_encoder(encoder):
    model = encoder['model']
    if 'symbols' in encoder:
        encode = encoder['symbols'].__getitem__
    elif model == 'bb':
        encode = BOOLEAN_SYMBOLS.__getitem__
    else:
        encode = loom.schema.MODELS[model].Value
    return encode


def load_decoder(encoder):
    model = encoder['model']
    if 'symbols' in encoder:
        decoder = {value: key for key, value in encoder['symbols'].iteritems()}
        decode = decoder.__getitem__
    elif model == 'bb':
        decode = ('0', '1').__getitem__
    else:
        decode = str
    return decode


def _make_encoder_builders_file((schema_in, rows_in)):
    assert os.path.isfile(rows_in)
    schema = json_load(schema_in)
    with csv_reader(rows_in) as reader:
        header = reader.next()
        builders = []
        seen = set()
        for name in header:
            if name in schema:
                if name in seen:
                    raise LoomError('Repeated column {} in csv file {}'.format(
                        name, rows_in))
                seen.add(name)
                model = schema[name]
                Builder = ENCODER_BUILDERS[model]
                builder = Builder(name, model)
            else:
                builder = None
            builders.append(builder)
        if all(builder is None for builder in builders):
            raise LoomError(
                'Csv file has no known features;'
                ', try adding a header to {}'.format(rows_in))
        missing_features = sorted(set(schema) - seen)
        if missing_features:
            raise LoomError('\n  '.join(
                ['Csv file is missing features:'] + missing_features))
        for row in reader:
            for value, builder in izip(row, builders):
                if builder is not None:
                    value = value.strip()
                    if value:
                        builder.add_value(value)
    return [b for b in builders if b is not None]


def _make_encoder_builders_dir(schema_in, rows_in):
    assert os.path.isdir(rows_in)
    files_in = [os.path.join(rows_in, f) for f in os.listdir(rows_in)]
    partial_builders = loom.util.parallel_map(_make_encoder_builders_file, [
        (schema_in, file_in)
        for file_in in files_in
    ])
    builders = partial_builders[0]
    for other_builders in partial_builders[1:]:
        assert len(builders) == len(other_builders)
        for builder, other in izip(builders, other_builders):
            assert builder.name == other.name
            builder += other
    return builders


def get_encoder_rank(encoder):
    rank = loom.schema.MODEL_RANK[encoder['model']]
    params = None
    if encoder['model'] == 'dd':
        # dd features must be ordered by increasing dimension
        params = len(encoder['symbols'])
    return (rank, params, encoder['name'])


@parsable.command
@loom.documented.transform(
    inputs=['ingest.schema', 'ingest.rows_csv'],
    outputs=['ingest.encoding'])
def make_encoding(schema_in, rows_in, encoding_out):
    '''
    Make a row encoder from csv rows data + json schema.
    '''
    if os.path.isdir(rows_in):
        builders = _make_encoder_builders_dir(schema_in, rows_in)
    else:
        builders = _make_encoder_builders_file((schema_in, rows_in))
    encoders = [builder.build() for builder in builders]
    encoders.sort(key=get_encoder_rank)
    json_dump(encoders, encoding_out)


def ensure_fake_encoders_are_sorted(encoders):
    dds = [e['symbols'] for e in encoders if e['model'] == 'dd']
    for smaller, larger in izip(dds, dds[1:]):
        if len(smaller) > len(larger):
            larger.update(smaller)


@parsable.command
@loom.documented.transform(
    inputs=['samples.0.model'],
    outputs=['ingest.schema'],
    role='test')
def make_schema(model_in, schema_out):
    '''
    Make a schema from a protobuf model.
    '''
    cross_cat = loom.schema_pb2.CrossCat()
    with open_compressed(model_in, 'rb') as f:
        cross_cat.ParseFromString(f.read())
    schema = {}
    for kind in cross_cat.kinds:
        featureid = iter(kind.featureids)
        for model in loom.schema.MODELS.iterkeys():
            for shared in getattr(kind.product_model, model):
                feature_name = '{:06d}'.format(featureid.next())
                schema[feature_name] = model
    json_dump(schema, schema_out)
    return schema


@parsable.command
def make_fake_encoding(schema_in, model_in, encoding_out):
    '''
    Make a fake encoding from json schema + model.
    Assume that feature names in schema correspond to featureids in model
    e.g. schema was generated from loom.format.make_schema
    '''
    schema = json_load(schema_in)
    fields = []
    builders = []
    name_to_builder = {}
    for name, model in sorted(schema.iteritems()):
        fields.append(loom.schema.MODEL_TO_DATATYPE[model])
        Builder = FAKE_ENCODER_BUILDERS[model]
        builder = Builder(name, model)
        builders.append(builder)
        name_to_builder[name] = builder

    cross_cat = loom.schema_pb2.CrossCat()
    with open_compressed(model_in, 'rb') as f:
        cross_cat.ParseFromString(f.read())
    for kind in cross_cat.kinds:
        featureid = iter(kind.featureids)
        for model in loom.schema.MODELS.iterkeys():
            for shared in getattr(kind.product_model, model):
                feature_name = '{:06d}'.format(featureid.next())
                assert feature_name in schema
                if model == 'dd':
                    for i in range(len(shared.alphas)):
                        name_to_builder[feature_name].add_value(str(i))
                elif model == 'dpd':
                    for val in shared.values:
                        name_to_builder[feature_name].add_value(str(val))
    encoders = [b.build() for b in builders]
    ensure_fake_encoders_are_sorted(encoders)
    json_dump(encoders, encoding_out)


def _import_dir(import_file, args):
    rows_csv_in, file_out, id_offset, id_stride, misc = args
    assert os.path.isdir(rows_csv_in)
    parts_in = sorted(
        os.path.abspath(os.path.join(rows_csv_in, f))
        for f in os.listdir(rows_csv_in)
    )
    part_count = len(parts_in)
    assert part_count > 0, 'no files in {}'.format(rows_csv_in)
    parts_out = []
    tasks = []
    for i, part_in in enumerate(parts_in):
        part_out = 'part.{}.{}'.format(i, os.path.basename(file_out))
        offset = id_offset + id_stride * i
        stride = id_stride * part_count
        parts_out.append(part_out)
        tasks.append((part_in, part_out, offset, stride, misc))
    with tempdir():
        loom.util.parallel_map(import_file, tasks)
        # It is safe use open instead of open_compressed even for .gz files;
        # see http://stackoverflow.com/questions/8005114
        with open(file_out, 'wb') as whole:
            for part_out in parts_out:
                with open(part_out, 'rb') as part:
                    shutil.copyfileobj(part, whole)
                os.remove(part_out)


def _import_rows(import_file, rows_csv_in, file_out, misc):
    rows_csv_in = os.path.abspath(rows_csv_in)
    file_out = os.path.abspath(file_out)
    id_offset = 0
    id_stride = 1
    args = (rows_csv_in, file_out, id_offset, id_stride, misc)
    if os.path.isdir(rows_csv_in):
        _import_dir(import_file, args)
    else:
        import_file(args)


def _import_rowids_file(args):
    rows_csv_in, rowids_out, id_offset, id_stride, id_field = args
    assert os.path.isfile(rows_csv_in)
    with csv_reader(rows_csv_in) as reader:
        header = reader.next()
        if id_field is None:
            basename = os.path.basename(rows_csv_in)
            get_rowid = lambda i, row: '{}:{}'.format(basename, i)
        else:
            pos = header.index(id_field)
            get_rowid = lambda i, row: row[pos]
        with csv_writer(rowids_out) as writer:
            for i, row in enumerate(reader):
                writer.writerow((id_offset + id_stride * i, get_rowid(i, row)))


@parsable.command
@loom.documented.transform(
    inputs=['ingest.rows_csv'],
    outputs=['ingest.rowids'])
def import_rowids(rows_csv_in, rowids_out, id_field=None):
    '''
    Import rowids from csv format to rowid index csv format.
    rows_csv_in can be a csv file or a directory containing csv files.
    Any csv file may be be raw .csv, or compressed .csv.gz or .csv.bz2.
    '''
    _import_rows(_import_rowids_file, rows_csv_in, rowids_out, id_field)


def _import_rows_file(args):
    rows_csv_in, rows_out, id_offset, id_stride, encoding_in = args
    assert os.path.isfile(rows_csv_in)
    encoders = json_load(encoding_in)
    message = loom.cFormat.Row()
    add_field = {
        'booleans': message.add_booleans,
        'counts': message.add_counts,
        'reals': message.add_reals,
    }
    with csv_reader(rows_csv_in) as reader:
        feature_names = list(reader.next())
        header_length = len(feature_names)
        name_to_pos = {name: i for i, name in enumerate(feature_names)}
        schema = []
        for encoder in encoders:
            pos = name_to_pos.get(encoder['name'])
            add = add_field[loom.schema.MODEL_TO_DATATYPE[encoder['model']]]
            encode = load_encoder(encoder)
            if encode == int:
                encode = lambda s: int(float(s))
            schema.append((pos, add, encode))

        def rows():
            for i, row in enumerate(reader):
                if len(row) != header_length:
                    raise LoomError('row {} has wrong length {}:\n{}'.format(
                        i, len(row), row))
                message.id = id_offset + id_stride * i
                for pos, add, encode in schema:
                    value = None if pos is None else row[pos].strip()
                    observed = bool(value)
                    message.add_observed(observed)
                    if observed:
                        add(encode(value))
                yield message
                message.Clear()

        loom.cFormat.row_stream_dump(rows(), rows_out)


@parsable.command
@loom.documented.transform(
    inputs=['ingest.encoding', 'ingest.rows_csv'],
    outputs=['ingest.rows'])
def import_rows(encoding_in, rows_csv_in, rows_out):
    '''
    Import rows from csv format to protobuf-stream format.
    rows_csv_in can be a csv file or a directory containing csv files.
    Any csv file may be be raw .csv, or compressed .csv.gz or .csv.bz2.
    '''
    _import_rows(_import_rows_file, rows_csv_in, rows_out, encoding_in)


@parsable.command
@loom.documented.transform(
    inputs=['ingest.encoding', 'ingest.rows'],
    outputs=['ingest.rows_csv'],
    role='test')
def export_rows(encoding_in, rows_in, rows_csv_out, chunk_size=1000000):
    '''
    Export rows from gzipped-protobuf-stream to directory-of-gzipped-csv-files.
    '''
    rows_csv_out = os.path.abspath(rows_csv_out)
    if rows_csv_out == os.getcwd():
        raise LoomError('Cannot export_rows to working directory')
    for ext in ['.csv', '.gz', '.bz2']:
        if rows_csv_out.endswith(ext):
            raise LoomError(
                'Expected rows_csv_out to be a dirname, actual'.format(
                    rows_csv_out))
    if not (chunk_size > 0):
        raise LoomError('Invalid chunk_size {}, must be positive'.format(
            chunk_size))
    encoders = json_load(encoding_in)
    fields = [loom.schema.MODEL_TO_DATATYPE[e['model']] for e in encoders]
    decoders = [load_decoder(e) for e in encoders]
    header = ['_id'] + [e['name'] for e in encoders]
    if os.path.exists(rows_csv_out):
        shutil.rmtree(rows_csv_out)
    os.makedirs(rows_csv_out)
    row_count = sum(1 for _ in protobuf_stream_load(rows_in))
    rows = loom.cFormat.row_stream_load(rows_in)
    chunk_count = (row_count + chunk_size - 1) / chunk_size
    chunks = sorted(
        os.path.join(rows_csv_out, 'rows.{}.csv.gz'.format(i))
        for i in xrange(chunk_count)
    )
    with ExitStack() as stack:
        with_ = stack.enter_context
        writers = [with_(csv_writer(f)) for f in chunks]
        for writer in writers:
            writer.writerow(header)
        for row, writer in izip(rows, cycle(writers)):
            data = row.iter_data()
            schema = izip(data['observed'], fields, decoders)
            csv_row = [row.id]
            for observed, field, decode in schema:
                csv_row.append(decode(data[field].next()) if observed else '')
            writer.writerow(csv_row)


if __name__ == '__main__':
    parsable.dispatch()
