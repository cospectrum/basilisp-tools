"""Generate deterministic CPU training and ONNX inference projects for the ML audit.

Example: python scripts/generate_ml_projects.py --output /tmp/ml-large --size 128
Then pass /tmp/ml-large/cases.json to check_ml_packages.py --manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def training(size):
    forms = ["(ns ml-audit.torch-large (:import [torch] [torch.nn :as nn] "
             "[torch.nn.functional :as functional] [torch.optim :as optim]))"]
    for index in range(size):
        forms.append(f"""(defn batch-{index} []
  (let [inputs (torch/ones 2 3)
        layer (nn/Linear 3 2)
        optimizer (optim/SGD (.parameters layer) ** :lr 0.01)
        outputs (functional/relu (layer inputs))
        loss (.sum outputs)]
    (.zero_grad optimizer)
    (.backward loss)
    (.step optimizer)
    (int (.numel outputs))))""")
    return "\n\n".join(forms) + "\n", "(+ " + " ".join(f"(batch-{index})" for index in range(size)) + ")\n"


def inference(size):
    forms = ["(ns ml-audit.onnx-large (:import [numpy :as np] [onnx] [onnx.checker :as checker] "
             "[onnx.helper :as helper] [onnx.reference :as reference] [onnx.shape_inference :as shapes]))"]
    for index in range(size):
        forms.append(f"""(defn predict-{index} []
  (let [input (helper/make_tensor_value_info "x" (.-FLOAT onnx/TensorProto) #py [3])
        output (helper/make_tensor_value_info "y" (.-FLOAT onnx/TensorProto) #py [3])
        node (helper/make_node "Identity" #py ["x"] #py ["y"])
        graph (helper/make_graph #py [node] "identity-{index}" #py [input] #py [output])
        model (helper/make_model graph)
        _checked (checker/check_model model)
        shaped (shapes/infer_shapes model)
        evaluator (reference/ReferenceEvaluator shaped)
        values (np/array #py [1 2 3] ** :dtype np/float32)]
    (int (.sum (first (.run evaluator nil #py {{"x" values}}))))))""")
    return "\n\n".join(forms) + "\n", "(+ " + " ".join(f"(predict-{index})" for index in range(size)) + ")\n"


def generate(size):
    if size < 1:
        raise ValueError("size must be positive")
    train_source, train_call = training(size)
    predict_source, predict_call = inference(size)
    yield {
        "name": "torch-large", "source": train_source + train_call, "expected": str(size * 4),
        "metadata": {"module": "torch.nn.functional", "path": ["relu"], "result_name": "Tensor"},
        "required_members": ["parameters", "sum", "zero_grad", "backward", "step", "numel"],
    }
    yield {
        "name": "onnx-large", "source": predict_source + predict_call, "expected": str(size * 6),
        "metadata": {"module": "onnx.helper", "path": ["make_model"], "result_name": "ModelProto"},
        "required_members": ["run"],
    }
    yield {
        "name": "torch-large-negative", "diagnostic": "invalid-arity",
        "source": train_source.replace("ml-audit.torch-large", "ml-audit.torch-large-negative", 1) + "(torch/tensor)\n",
    }
    yield {
        "name": "onnx-large-negative", "diagnostic": "type-mismatch",
        "source": predict_source.replace("ml-audit.onnx-large", "ml-audit.onnx-large-negative", 1) + '(helper/make_node 42 #py [] #py [])\n',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=128, help="Training functions and inference graphs per project.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases = list(generate(args.size))
    inventory = []
    for case in cases:
        path = args.output / (case["name"].replace("-", "_") + ".lpy")
        path.write_text(case["source"])
        inventory.append({"filename": path.name, "blocks": args.size, "bytes": len(path.read_bytes()),
                          "lines": len(case["source"].splitlines()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (args.output / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
    (args.output / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    print(json.dumps(inventory))


if __name__ == "__main__":
    main()
