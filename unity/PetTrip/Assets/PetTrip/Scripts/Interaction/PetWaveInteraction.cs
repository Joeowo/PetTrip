using UnityEngine;

namespace PetTrip
{
    public sealed class PetWaveInteraction : MonoBehaviour
    {
        public bool WasTriggered { get; private set; }

        public void Trigger()
        {
            WasTriggered = true;
            transform.localScale = new Vector3(1.15f, 1.15f, 1f);
            Debug.Log("PETTRIP_INTERACTION_OK id=pet_wave");
        }

        private void OnMouseDown()
        {
            Trigger();
        }
    }
}
