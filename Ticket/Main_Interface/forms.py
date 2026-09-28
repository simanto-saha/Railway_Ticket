from django import forms
from .models import Profile


class ProfileImageForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['profile_image']

    def clean_profile_image(self):
        img = self.cleaned_data.get('profile_image')
        if img and hasattr(img, 'size'):
            if img.size > 2 * 1024 * 1024:
                raise forms.ValidationError("Image 2MB er cheye boro hobe na.")
            if img.content_type not in ('image/jpeg', 'image/png', 'image/webp'):
                raise forms.ValidationError("Only JPG, PNG or WEBP allowed.")
        return img