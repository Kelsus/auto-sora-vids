import base64
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add src to path
sys.path.append("src")

from aivideomaker.chart_planner.image_ingestor import ImageIngestor
from aivideomaker.script_engine.llm import LLMClient

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create dummy image
RED_DOT = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82'
IMAGE_PATH = Path("test_chart.png")
with open(IMAGE_PATH, "wb") as f:
    f.write(RED_DOT)

class MockLLM(LLMClient):
    def complete(self, prompt: str, **kwargs) -> str:
        return "{}"
        
    def complete_with_images(self, prompt: str, images: list[tuple[str, str]], **kwargs) -> str:
        logger.info(f"MockLLM received {len(images)} images")
        return """
        {
            "title": "Test Chart",
            "summary": "A test chart showing a red dot.",
            "reason": "Testing the pipeline.",
            "keywords": ["test", "red", "dot"]
        }
        """

def test_ingestor():
    logger.info("Testing ImageIngestor...")
    llm = MockLLM()
    ingestor = ImageIngestor(llm)
    
    plan = ingestor.analyze_images([IMAGE_PATH])
    
    assert len(plan.charts) == 1
    chart = plan.charts[0]
    assert chart.title == "Test Chart"
    assert chart.image_path == str(IMAGE_PATH.absolute())
    assert chart.variant == "image"
    
    logger.info("✅ ImageIngestor test passed!")

if __name__ == "__main__":
    try:
        test_ingestor()
    finally:
        if IMAGE_PATH.exists():
            IMAGE_PATH.unlink()
