def build_vocab(text):
    word_to_id = {}
    for token in text.split():
        if token not in word_to_id:
            word_to_id[token] = len(word_to_id) + 1
    return word_to_id


def decode_ids(ids, word_to_id, unknown_token="<UNK>"):
    id_to_word = {id_: word for word, id_ in word_to_id.items()}
    return [id_to_word.get(i, unknown_token) for i in ids]



if __name__ == "__main__":
    text = "Wenn du willst, kann ich dir auch erklären, wie „Score“ genau berechnet wird (BM25) — das ist der nächste wichtige Baustein."
    word_to_id = build_vocab(text)
    print(build_vocab(text))

    ids = [1, 2, 3, 999]
    decoded_words = decode_ids(ids, word_to_id)
    print(ids)
    print(" ".join(decoded_words))