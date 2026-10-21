import org.junit.jupiter.api.Test; import static org.junit.jupiter.api.Assertions.*; public class CalculatorTest { @Test void testAdd() { assertEquals(5, new Calculator().add(2, 3)); } }
