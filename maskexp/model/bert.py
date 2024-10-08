"""
Reference: https://github.com/StepanTita/nano-BERT

NOTICE: The implementation of attention mechanism is not optimized for speed!!

TODO:
1. Improve code efficiency


"""

import torch
import torch.nn as nn
from collections import defaultdict
from typing import Any, Mapping, Optional
import torch.nn.functional as F
import math
from maskexp.definitions import IGNORE_LABEL_INDEX


class BertEmbeddings(torch.nn.Module):
    def __init__(self, vocab_size, n_embed=3, max_seq_len=16):
        super().__init__()
        self.max_seq_len = max_seq_len

        self.word_embeddings = torch.nn.Embedding(vocab_size, n_embed)
        self.pos_embeddings = torch.nn.Embedding(max_seq_len, n_embed)

        self.layer_norm = torch.nn.LayerNorm(n_embed, eps=1e-12, elementwise_affine=True)
        self.dropout = torch.nn.Dropout(p=0.1, inplace=False)

    def forward(self, x):
        position_ids = torch.arange(self.max_seq_len, dtype=torch.long, device=x.device)

        words_embeddings = self.word_embeddings(x)
        position_embeddings = self.pos_embeddings(position_ids)

        embeddings = words_embeddings + position_embeddings
        embeddings = self.layer_norm(embeddings)
        embeddings = self.dropout(embeddings)

        return embeddings


class BertAttentionHead(torch.nn.Module):
    """
    A single attention head in MultiHeaded Self Attention layer.
    The idea is identical to the original paper ("Attention is all you need"),
    however instead of implementing multiple heads to be evaluated in parallel we matrix multiplication,
    separated in a distinct class for easier and clearer interpretability
    """

    def __init__(self, head_size, dropout=0.1, n_embed=3):
        super().__init__()

        self.query = torch.nn.Linear(in_features=n_embed, out_features=head_size)
        self.key = torch.nn.Linear(in_features=n_embed, out_features=head_size)
        self.values = torch.nn.Linear(in_features=n_embed, out_features=head_size)

        self.dropout = torch.nn.Dropout(dropout)

    def forward(self, x, mask):
        # B, Seq_len, N_embed
        B, seq_len, n_embed = x.shape

        q = self.query(x)
        k = self.key(x)
        v = self.values(x)

        weights = (q @ k.transpose(-2, -1)) / math.sqrt(n_embed)  # (B, Seq_len, Seq_len)
        weights = weights.masked_fill(mask == 0, -1e9)  # mask out not attended tokens

        scores = F.softmax(weights, dim=-1)
        scores = self.dropout(scores)

        context = scores @ v

        return context


class BertSelfAttention(torch.nn.Module):
    """
    MultiHeaded Self-Attention mechanism as described in "Attention is all you need"
    """

    def __init__(self, n_heads=1, dropout=0.1, n_embed=3):
        super().__init__()

        head_size = n_embed // n_heads
        n_heads = n_heads
        self.heads = torch.nn.ModuleList([BertAttentionHead(head_size, dropout, n_embed) for _ in range(n_heads)])
        self.proj = torch.nn.Linear(head_size * n_heads, n_embed)  # project from multiple heads to the single space
        self.dropout = torch.nn.Dropout(dropout)

    def forward(self, x, mask):
        context = torch.cat([head(x, mask) for head in self.heads], dim=-1)
        proj = self.proj(context)
        out = self.dropout(proj)

        return out


class FeedForward(torch.nn.Module):
    def __init__(self, dropout=0.1, n_embed=3):
        super().__init__()

        self.ffwd = torch.nn.Sequential(
            torch.nn.Linear(n_embed, 4 * n_embed),
            torch.nn.GELU(),
            torch.nn.Linear(4 * n_embed, n_embed),
            torch.nn.Dropout(dropout),
        )

    def forward(self, x):
        out = self.ffwd(x)

        return out


class BertLayer(torch.nn.Module):
    """
    Single layer of BERT transformer model
    """

    def __init__(self, n_heads=1, dropout=0.1, n_embed=3):
        super().__init__()

        # unlike in the original paper, today in transformers it is more common to apply layer norm before other layers
        # this idea is borrowed from Andrej Karpathy's series on transformers implementation
        self.layer_norm1 = torch.nn.LayerNorm(n_embed)
        self.self_attention = BertSelfAttention(n_heads, dropout, n_embed)

        self.layer_norm2 = torch.nn.LayerNorm(n_embed)
        self.feed_forward = FeedForward(dropout, n_embed)

    def forward(self, x, mask):
        x = self.layer_norm1(x)
        x = x + self.self_attention(x, mask)

        x = self.layer_norm2(x)
        out = x + self.feed_forward(x)

        return out


class BertEncoder(torch.nn.Module):
    def __init__(self, n_layers=2, n_heads=1, dropout=0.1, n_embed=3):
        super().__init__()

        self.layers = torch.nn.ModuleList([BertLayer(n_heads, dropout, n_embed) for _ in range(n_layers)])

    def forward(self, x, mask):
        for layer in self.layers:
            x = layer(x, mask)

        return x


class BertPooler(torch.nn.Module):
    def __init__(self, dropout=0.1, n_embed=3):
        super().__init__()

        self.dense = torch.nn.Linear(in_features=n_embed, out_features=n_embed)
        self.activation = torch.nn.GELU()

    def forward(self, x):
        pooled = self.dense(x)
        out = self.activation(pooled)

        return out


class NanoBERT(torch.nn.Module):
    """
    NanoBERT is a almost an exact copy of a transformer decoder part described in the paper "Attention is all you need"
    This is a base model that can be used for various purposes such as Masked Language Modelling, Classification,
    Or any other kind of NLP tasks.
    This implementation does not cover the Seq2Seq problem, but can be easily extended to that.
    """

    def __init__(self, vocab_size, n_layers=2, n_heads=1, dropout=0.1, n_embed=3, max_seq_len=16):
        """

        :param vocab_size: size of the vocabulary that tokenizer is using
        :param n_layers: number of BERT layer in the model (default=2)
        :param n_heads: number of heads in the MultiHeaded Self Attention Mechanism (default=1)
        :param dropout: hidden dropout of the BERT model (default=0.1)
        :param n_embed: hidden embeddings dimensionality (default=3)
        :param max_seq_len: max length of the input sequence (default=16)
        """
        super().__init__()

        self.embedding = BertEmbeddings(vocab_size, n_embed, max_seq_len)

        self.encoder = BertEncoder(n_layers, n_heads, dropout, n_embed)

        self.pooler = BertPooler(dropout, n_embed)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None):
        if attention_mask is None:
            attention_mask = torch.ones_like(input_ids)
        if token_type_ids is None:
            token_type_ids = torch.zeros_like(input_ids)

        emb_output = self.embedding(input_ids)

        # mask = (input_ids > 0).unsqueeze(1).repeat(1, input_ids.size(1), 1)

        attention_mask = attention_mask.unsqueeze(1)
        attention_mask = attention_mask.to(dtype=next(self.parameters()).dtype)
        attention_mask = attention_mask.repeat(1, input_ids.size(1), 1)

        # encoded = self.encoder(emb_output, mask)
        encoded = self.encoder(emb_output, attention_mask)

        pooled = self.pooler(encoded)
        return pooled


class NanoBertMLM(nn.Module):
    def __init__(self, vocab_size, n_layers=2, n_heads=1, dropout=0.1, n_embed=3, max_seq_len=16):
        super().__init__()
        self.bert = NanoBERT(vocab_size=vocab_size, n_layers=n_layers, n_heads=n_heads, dropout=dropout,
                             n_embed=n_embed, max_seq_len=max_seq_len)
        self.cls = nn.Sequential(
            nn.Linear(in_features=n_embed, out_features=n_embed),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(in_features=n_embed, out_features=vocab_size)
        )

    def forward(self, input_ids, token_type_ids=None, attention_mask=None, labels=None):
        sequence_output = self.bert(input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids)
        prediction_scores = self.cls(sequence_output)

        loss = None
        if labels is not None:
            loss_fct = nn.CrossEntropyLoss(ignore_index=IGNORE_LABEL_INDEX)
            loss = loss_fct(prediction_scores.view(-1, self.cls[-1].out_features), labels.view(-1))

        return loss, prediction_scores


def emd_loss(prediction_scores, labels, token_mask):
    """
    Compute Earth Mover's Distance loss for ordinal classification.

    :param prediction_scores:
    :param labels:
    :param token_mask:
    :return:
    """
    prediction_scores = prediction_scores[token_mask]
    labels = labels[token_mask]

    num_classes = prediction_scores.size(-1)

    # Convert labels to one-hot encoding
    cumulative_true = F.one_hot(labels, num_classes=num_classes).to(torch.float32).cumsum(dim=1)
    cumulative_pred = prediction_scores.cumsum(dim=1)

    emd = torch.abs(cumulative_true - cumulative_pred).sum(dim=1)
    return emd.mean() / num_classes * 2  # scale to a similar range


class LossWeighting():
    def __init__(self, weights: Mapping[str, float] or None = None) -> None:
        self.weights = weights if weights is not None else defaultdict(lambda: 1.)

    def on_train_batch_end(self,
                           trainer,
                           outputs: Any,
                           batch: Any,
                           batch_idx: int) -> None:
        print({f"hparams/{k}_weight": v for k, v in self.weights.items()})

    def combine_losses(self, **losses):
        self.update_weights(losses)
        return sum([self.weights[key] * losses[key] for key in self.weights.keys()])

    def update_weights(self, losses):
        pass

    def __str__(self):
        params = '\n'.join(f"\t{k}: {v}" for k, v in vars(self).items())
        return self.__class__.__name__ + "(\n" + params + "\n)"


class NanoBertMLMOrdinalLoss(nn.Module):
    def __init__(self, vocab_size, n_layers=2, n_heads=1, dropout=0.1, n_embed=3, max_seq_len=16,
                 idx_ord_start=356, idx_ord_end=387):
        super().__init__()
        self.bert = NanoBERT(vocab_size=vocab_size, n_layers=n_layers, n_heads=n_heads, dropout=dropout,
                             n_embed=n_embed, max_seq_len=max_seq_len)
        self.cls = nn.Sequential(
            nn.Linear(in_features=n_embed, out_features=n_embed),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(in_features=n_embed, out_features=vocab_size)
        )

    def forward(self, input_ids, token_type_ids=None, attention_mask=None, labels=None):
        sequence_output = self.bert(input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids)
        prediction_scores = self.cls(sequence_output)

        loss = None
        ce_loss = nn.CrossEntropyLoss(ignore_index=IGNORE_LABEL_INDEX)
        if labels is not None:
            # Flatten prediction scores and labels for Cross Entropy Loss
            flat_prediction_scores = prediction_scores.view(-1, self.cls[-1].out_features)
            flat_labels = labels.view(-1)

            # Compute mask for special tokens (id=1) and non-special tokens
            if token_type_ids is not None:
                special_token_mask = token_type_ids.view(-1) == 1  # Mask for special tokens
                non_special_token_mask = ~special_token_mask  # Mask for non-special tokens

                # Compute loss for special tokens using EMD
                emd_loss_value = emd_loss(
                    prediction_scores.view(-1, self.cls[-1].out_features),
                    flat_labels,
                    special_token_mask
                )

                # Compute loss for non-special tokens using Cross Entropy Loss
                cross_entropy_loss_value = ce_loss(
                    flat_prediction_scores[non_special_token_mask],
                    flat_labels[non_special_token_mask]
                )

                # Combine the losses
                loss = emd_loss_value + cross_entropy_loss_value
            else:
                # Default to Cross Entropy Loss if no token_type_ids are provided
                loss = ce_loss(flat_prediction_scores, flat_labels)

        return loss, prediction_scores


def test_bert():
    # Test configuration
    vocab_size = 100
    max_seq_len = 16
    batch_size = 8

    # Initialize model
    model = NanoBertMLM(vocab_size=vocab_size)

    # Generate random input data
    input_ids = torch.randint(low=0, high=vocab_size, size=(batch_size, max_seq_len))
    token_type_ids = torch.zeros_like(input_ids)  # Dummy token type ids
    attention_mask = torch.ones_like(input_ids)  # Dummy attention mask
    labels = torch.randint(low=0, high=vocab_size, size=(batch_size, max_seq_len))

    # Optionally set some labels to ignore index to simulate masked labels
    labels[torch.rand_like(labels, dtype=torch.float) < 0.2] = IGNORE_LABEL_INDEX

    # Perform a forward pass
    model.eval()  # Set model to evaluation mode
    with torch.no_grad():
        loss, prediction_scores = model(input_ids, token_type_ids=token_type_ids, attention_mask=attention_mask,
                                        labels=labels)

    # Check output
    print(f"Loss: {loss.item() if loss is not None else 'N/A'}")
    print(f"Prediction scores shape: {prediction_scores.shape}")

    # Assertions to verify correct output
    assert prediction_scores.shape == (batch_size, max_seq_len, vocab_size), "Incorrect prediction scores shape"
    if loss is not None:
        assert isinstance(loss.item(), float), "Loss is not a float value"


# Run the test function
if __name__ == '__main__':
    test_bert()
