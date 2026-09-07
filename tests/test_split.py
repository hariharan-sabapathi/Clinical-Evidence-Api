from clinical_retrieval.evalset.split import stratified_patient_split


def test_split_covers_every_patient_exactly_once():
    counts = {f"p{i}": i + 1 for i in range(100)}
    folds = stratified_patient_split(counts)
    all_assigned = folds["train"] + folds["dev"] + folds["test"]
    assert sorted(all_assigned) == sorted(counts.keys())
    assert len(all_assigned) == len(set(all_assigned))


def test_split_sizes_are_roughly_60_20_20():
    counts = {f"p{i}": i + 1 for i in range(120)}
    folds = stratified_patient_split(counts)
    assert abs(len(folds["train"]) - 72) <= 1
    assert abs(len(folds["dev"]) - 24) <= 1
    assert abs(len(folds["test"]) - 24) <= 1


def test_split_spreads_high_and_low_encounter_patients_across_folds():
    counts = {f"p{i}": i + 1 for i in range(120)}  # encounter counts 1..120
    folds = stratified_patient_split(counts)
    for fold_name in ("train", "dev", "test"):
        fold_counts = [counts[p] for p in folds[fold_name]]
        # each fold should span a wide range, not cluster at one end
        assert max(fold_counts) - min(fold_counts) > 50
