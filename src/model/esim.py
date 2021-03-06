# coding: utf-8

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""
Build an Enhancing LSTM model for Natural Language Inference
"""

__all__ = ['ESIM']

from mxnet.gluon import nn, rnn

EPS = 1e-12


class ESIMClassifier(nn.HybridBlock):
    """"Enhanced LSTM for Natural Language Inference" Qian Chen,
    Xiaodan Zhu, Zhenhua Ling, Si Wei, Hui Jiang, Diana Inkpen. ACL (2017)

    Parameters
    ----------
    vocab_size: int
        Number of words in vocab
    word_embed_size : int
        Dimension of word vector
    hidden_size : int
        Number of hidden units in lstm cell
    dense_size : int
        Number of hidden units in dense layer
    num_classes : int
        Number of categories
    dropout : int
        Dropout prob
    """

    def __init__(self, vocab_size, num_classes, word_embed_size, hidden_size, dense_size,
                 dropout=0., **kwargs):
        super().__init__(**kwargs)
        with self.name_scope():
            self.embedding = nn.Embedding(vocab_size, word_embed_size)
            self.embedding_dropout = nn.Dropout(dropout, axes=1)
            self.lstm_encoder1 = rnn.LSTM(hidden_size, input_size=word_embed_size, bidirectional=True, layout='NTC')
            self.ff_proj = nn.Dense(hidden_size, in_units=hidden_size * 2 * 4, flatten=False, activation='relu')
            self.lstm_encoder2 = rnn.LSTM(hidden_size, input_size=hidden_size, bidirectional=True, layout='NTC')

            self.classifier = nn.HybridSequential()
            if dropout:
                self.classifier.add(nn.Dropout(rate=dropout))
            self.classifier.add(nn.Dense(units=hidden_size, activation='relu'))
            if dropout:
                self.classifier.add(nn.Dropout(rate=dropout))
            self.classifier.add(nn.Dense(units=num_classes))

    def _soft_attention_align(self, F, x1, x2): #valid_len1, valid_len2):
        # attention shape: (batch, x1_seq_len, x2_seq_len)
        attention = F.batch_dot(x1, x2, transpose_b=True)

        ## masking
        #a_mask_1 = F.SequenceMask(attention, sequence_length=valid_len1, use_sequence_length=True, axis=1, value=EPS)
        #a_mask_1_2 = F.SequenceMask(a_mask_1.transpose([0, 2, 1]), sequence_length=valid_len2, use_sequence_length=True, axis=1, value=EPS).transpose([0, 2, 1])
        #attention = a_mask_1_2

        x1_align = F.batch_dot(attention.softmax(), x2)
        x2_align = F.batch_dot(attention.transpose([0, 2, 1]).softmax(), x1)

        return x1_align, x2_align

    def _submul(self, F, x1, x2):
        mul = x1 * x2
        sub = x1 - x2

        return F.concat(mul, sub, dim=-1)

    def _pool(self, F, x):
        p1 = x.mean(axis=1)
        p2 = x.max(axis=1)

        return F.concat(p1, p2, dim=-1)

    def hybrid_forward(self, F, x1, x2): #, valid_len1, valid_len2):  # pylint: disable=arguments-differ
        # x1_embed x2_embed shape: (batch, seq_len, word_embed_size)
        x1_embed = self.embedding_dropout(self.embedding(x1))
        x2_embed = self.embedding_dropout(self.embedding(x2))

        x1_lstm_encode = self.lstm_encoder1(x1_embed)
        x2_lstm_encode = self.lstm_encoder1(x2_embed)

        # attention
        x1_algin, x2_algin = self._soft_attention_align(F, x1_lstm_encode, x2_lstm_encode)
                                                        #valid_len1, valid_len2)

        # compose
        x1_combined = F.concat(x1_lstm_encode, x1_algin,
                               self._submul(F, x1_lstm_encode, x1_algin), dim=-1)
        x2_combined = F.concat(x2_lstm_encode, x2_algin,
                               self._submul(F, x2_lstm_encode, x2_algin), dim=-1)

        x1_compose = self.lstm_encoder2(self.ff_proj(x1_combined))
        x2_compose = self.lstm_encoder2(self.ff_proj(x2_combined))

        # aggregate
        x1_agg = self._pool(F, x1_compose)
        x2_agg = self._pool(F, x2_compose)

        # fully connection
        output = self.classifier(F.concat(x1_agg, x2_agg, dim=-1))

        return output
